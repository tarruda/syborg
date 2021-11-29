#!/usr/bin/python3
import argparse
import configparser
import contextlib
import hashlib
import itertools
import os
import pathlib
import signal
import sys
import typing

import pexpect
from xdg.BaseDirectory import xdg_config_home
from ush.sh import borg, rclone, keyctl, echo
from ush import sh, NULL


DEFAULT_CONFIG = '''
[DEFAULT]
borg.create.archive = ${archive}-{hostname}-{now:%Y-%m-%d-%H%M}
borg.create.filter = AME
borg.create.compression = zstd
borg.create.verbose = yes
borg.create.stats = yes
borg.create.progress = yes
borg.create.show-rc = yes
borg.create.one-file-system = yes
borg.create.exclude-caches = yes
borg.prune.list = yes
borg.prune.prefix = ${archive}-{hostname}-
borg.prune.show-rc = yes
borg.prune.keep-last = 2
'''

def log(*args):
    print(*args, file=sys.stderr)
    sys.stderr.flush()


def die(*args, exit_code=2):
    log(*args)
    sys.exit(exit_code)


def get_config_list(config, section, key, allow_empty=False):
    def iterator():
        for line in config.get(section, key, fallback='').split('\n'):
            for piece in line.split(','):
                stripped = piece.strip()
                if stripped:
                    yield stripped
    rv = list(iterator())
    if not rv and not allow_empty:
        die('{}.{} is empty'.format(section, key))
    return rv


@contextlib.contextmanager
def ssh_agent():
    sh.alias(ssh_agent='ssh-agent')
    agent_details = list(sh.ssh_agent('-s'))
    agent_socket = agent_details[0].split(';')[0].split('=')[1]
    agent_pid = int(agent_details[1].split(';')[0].split('=')[1])
    os.environ['SSH_AUTH_SOCK'] = agent_socket
    yield agent_socket
    del os.environ['SSH_AUTH_SOCK']
    os.kill(agent_pid, signal.SIGTERM)


def parse_backup(subparsers):
    parser = subparsers.add_parser('backup')
    parser.add_argument('backup', help='Backup configuration name')
    parser.set_defaults(func=backup)


def parse_wrapped_command(subparsers, wrapper_name, cmd):
    parser = subparsers.add_parser(wrapper_name)
    parser.add_argument('repository', help='Repository name')
    parser.add_argument('extra_args', nargs='*')
    parser.set_defaults(
            func=lambda args, config: wrapped_command(args, config, cmd))
    return parser


def parse_args(argv):
    if not os.environ.get('SYBORG_CONFIG'):
        os.environ['SYBORG_CONFIG'] = '{}/syborg/syborg.cfg'.format(
                xdg_config_home)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.environ['SYBORG_CONFIG'],
            help='syborg main config file')
    subparsers = parser.add_subparsers()
    subparsers.required = True
    subparsers.dest = 'command'
    parse_backup(subparsers)
    parse_wrapped_command(subparsers, 'list', borg('list'))
    parse_wrapped_command(subparsers, 'info', borg('info'))
    parse_wrapped_command(subparsers, 'mount', borg('mount'))
    parse_wrapped_command(subparsers, 'check', borg('check'))
    args = parser.parse_args(argv)
    return args


def sha1(data):
    h = hashlib.sha1()
    h.update(data.encode('utf8'))
    return h.hexdigest()


def get_keyname(passcommand):
    return 'syborg-{}'.format(sha1(passcommand))


def get_stored_passphrase_key_id(passcommand):
    keyname = get_keyname(passcommand)
    try:
        # check if the passphrase for this repository is already stored in the
        # keyring
        return str(keyctl('search', '@u', 'user', keyname)).strip()
    except Exception:
        return None


def store_passphrase(passcommand, passphrase):
    keyname = get_keyname(passcommand)
    # store in the session keyring and fetch the key id.
    key_id = str(echo(passphrase) |
                 keyctl('padd', 'user', keyname, '@s')).strip()
    # run some extra steps to make the key available in future invocations
    # by transfering to the user keyring. source:
    # https://mjg59.dreamwidth.org/37333.html
    # TODO investigate if it is possible to improve security by using a
    # keyring that is private to this service
    keyctl('setperm', key_id, '0x3f3f0000')()
    keyctl('timeout', key_id, '86400')()
    keyctl('link', key_id, '@u')()
    keyctl('unlink', key_id, '@s')()
    return key_id


def cache_passphrase(env, passcommand, test_passphrase):
    key_id = get_stored_passphrase_key_id(passcommand)
    if not key_id:
        tries = 0
        while True:
            # not stored yet. run "passcommand" to retrive the passphrase
            passphrase = str(sh.sh('-c', passcommand, env=env)).strip()
            if test_passphrase(passphrase):
                break
            tries += 1
            if tries == 3:
                die('"{}" provided wrong passphrase'.format(passcommand))
        key_id = store_passphrase(passcommand, passphrase)
    return 'keyctl pipe {}'.format(key_id)


def cache_borg_passphrase(env):
    passcommand = env.get('BORG_PASSCOMMAND')
    repo = env.get('BORG_REPO')
    if not (repo and passcommand):
        return
    def test(passphrase):
        code = (echo(passphrase) |
                borg('info', repo,
                     env={'BORG_PASSPHRASE_FD': '0'},
                     stdout=NULL,
                     raise_on_error=False))()[0]
        return code == 0
    # replace BORG_PASSCOMMAND with a keyctl call that will return the cached
    # passphrase
    env['BORG_PASSCOMMAND'] = cache_passphrase(env, passcommand, test)


def set_rclone_passphrase(env):
    passcommand = env.get('RCLONE_PASSWORD_COMMAND')
    if not passcommand:
        return
    def test(passphrase):
        code = rclone('config', 'dump', '--ask-password=false',
                stdout=NULL, env={'RCLONE_CONFIG_PASS': passphrase})[0]
        return code == 0
    env['RCLONE_PASSWORD_COMMAND'] = cache_passphrase(env, passcommand, test)


def ssh_add(env):
    passcommand = env.get('SYBORG_SSH_PASSCOMMAND')
    if not passcommand:
        return
    ssh_key = env.get('SYBORG_SSH_KEY')
    args = []
    if ssh_key:
        ssh_key = os.path.expanduser(ssh_key)
        args.append(ssh_key)
    ssh_add_proc = pexpect.spawn('ssh-add', args)
    cached = True
    def test(passphrase):
        nonlocal cached
        cached = False
        ssh_add_proc.sendline(passphrase)
        index = ssh_add_proc.expect([
            'Bad passphrase, try again for',
            'Identity added:'
            ])
        return index == 1
    ssh_add_proc.expect('Enter passphrase for')
    passphrase = str(sh.sh('-c', cache_passphrase(env, passcommand,
        test))).strip()
    if cached:
        ssh_add_proc.sendline(passphrase)
    ssh_add_proc.wait()


def config_section_keys(config, section, prefix):
    return ((key, key[len(prefix):]) for key in itertools.chain(
            config['DEFAULT'].keys(), config[section].keys())
            if key.startswith(prefix))


def repo_env(config, repository):
    section = 'repository.{}'.format(repository)
    rv = {}
    for key, env_name in config_section_keys(config, section, 'env.'):
        rv[env_name.upper()] = config.get(section, key).strip()
    if 'BORG_REPO' not in rv:
        die('BORG_REPO was not set for', repository)
    rv['BORG_REPO'] = os.path.expanduser(rv['BORG_REPO'])
    ssh_add(rv)
    cache_borg_passphrase(rv)
    return rv


def rclone_sync(borg_repo, mirrors, check):
    for mirror in mirrors:
        if check:
            borg('check', '--verbose', '--progress', '--verify-data')()
        rclone('sync', '--ask-password=false', '-v', borg_repo, mirror)()
        log('synced {} to {}'.format(borg_repo, mirror))


def extract_borg_commands_opts(config, command, section, boolean_opts,
                               exclude_opts):
    boolean_opts += [
            'critical', 'error', 'warning', 'info', 'v', 'verbose', 'debug',
            'p', 'progress', 'log-json', 'show-rc', 'consider-part-files'
            ]
    key_prefix = 'borg.{}.'.format(command)
    opts = {}
    for key, opt_name in config_section_keys(config, section, key_prefix):
        if opt_name in exclude_opts:
            continue
        if opt_name in boolean_opts:
            opt_value = config.getboolean(section, key)
        else:
            opt_value = config.get(section, key)
        opts[opt_name] = opt_value
    for k, v in opts.items():
        if v == False:
            continue
        yield '--' + k
        if v != True:
            yield v


def create(config, section, archive_section):
    archive = config.get(section, 'borg.create.archive', fallback=None)
    if not archive:
        die('borg.create.archive was not specified in section', section)
    opts = list(extract_borg_commands_opts(config, 'create', section, [
        'dry-run', 'stats', 'list', 'json', 'no-cache-sync', 'no-files-cache',
        'exclude-caches', 'keep-exclude-tags', 'keep-tag-files',
        'exclude-nodump', 'one-file-system', 'numeric-owner', 'noatime',
        'noctime', 'nobirthtime', 'nobsdflags', 'ignore-inode', 'read-special'
        ], ['e', 'exclude', 'exclude-from', 'pattern', 'patterns-from',
            'archive']))
    include = get_config_list(config, archive_section, 'include')
    exclude = get_config_list(config, archive_section, 'exclude')
    e_args = list(itertools.chain.from_iterable(('-e', e) for e in exclude))
    all_args = opts + e_args + ['::' + archive] + include
    log('running: borg create', *all_args)
    borg('create', *all_args)()


def prune(config, section):
    opts = list(extract_borg_commands_opts(config, 'prune', section, [
        'dry-run', 'force', 's', 'stats', 'list', 'save-space'], []))
    log('running: borg prune', *opts)
    borg('prune', *opts)()


def backup(args, config):
    section = 'backup.{}'.format(args.backup)
    if not config.has_section(section):
        die('syborg config has no section "{}"'.format(section))
    archives = get_config_list(config, section, 'archives')
    repositories = get_config_list(config, section, 'repositories')
    with ssh_agent():
        for repository in repositories:
            backup_repository(config, section, repository, archives)


def backup_repository(config, section, repository, archives):
    env = repo_env(config, repository)
    repo_section = 'repository.{}'.format(repository)
    mirrors = get_config_list(config, repo_section, 'rclone.mirrors',
                              allow_empty=True)
    rclone_check = config.get(repo_section, 'rclone.check', fallback=False)
    if mirrors:
        set_rclone_passphrase(env)
    with sh.setenv(env):
        for archive in archives:
            archive_section = 'archive.{}'.format(archive)
            cwd = os.path.expanduser(config.get(archive_section, 'basedir'))
            config.set(section, 'archive', archive)
            with sh.chdir(cwd):
                create(config, section, archive_section)
                prune(config, section)
        if mirrors:
            rclone_sync(env['BORG_REPO'], mirrors, rclone_check)


def wrapped_command(args, config, cmd):
    with ssh_agent():
        env = repo_env(config, args.repository)
        with sh.setenv(env):
            if cmd.argv[1] == 'mount':
                # borg mount doesn't use BORG_REPO env
                cmd = cmd(env['BORG_REPO'])
            if args.extra_args:
                cmd = cmd(*args.extra_args)
            cmd()


def main():
    args = parse_args(sys.argv[1:])
    config = configparser.ConfigParser(
            interpolation=configparser.ExtendedInterpolation())
    cfg_path = pathlib.Path(args.config)
    config.read_string(DEFAULT_CONFIG)
    config.read(str(cfg_path))
    args.func(args, config)


if __name__ == '__main__':
    main()
