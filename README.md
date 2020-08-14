# syborg

This is mainly a borg wrapper with some nice utilities:

- Backups are defined with configuration files
- Easily mix multiple archives with multiple repositories (see config example).
- Integrates with rclone, which allows maintaining a repository in a cloud
  storage provider such as dropbox.
- Caches repository keys, ssh keys and rclone config passwords in the kernel
  keyring of the user.
- Also wraps some other borg commands in an attempt to provide nice UI for
  interacting with defined repositories.

This tool is in part inspired by
[borgmatic](https://github.com/witten/borgmatic), but I've decide to write a new
tool that better attends my own needs.

## Usage

Here's a simple configuration file (adapted from borgmatic's own config
example):

    # /root/.config/syborg/syborg.cfg example
    [backup.example]
    archives = main
    repositories = borgbase,rsyncnet,local
    borg.prune.keep-daily = 7
    borg.prune.keep-weekly = 4
    borg.prune.keep-monthly = 6
    
    [archive.main]
    basedir = /
    include =
      home
      etc
    
    [repository.borgbase]
    env.BORG_REPO = k8pDxu32@k8pDxu32.repo.borgbase.com:repo
    
    [repository.rsyncnet]
    env.BORG_REPO = 1234@usw-s001.rsync.net:backups.borg
    env.BORG_REMOTE_PATH = borg1
    
    [repository.local]
    env.BORG_REPO = /var/lib/backups/local.borg


With the above config in place:

    syborg backup example

The "backup" subcommand is basically a wrapper around borg create/prune. If
"mirrors" are defined, rclone will be used to copy the repository to a remote
location (see [syborg-example.cfg](syborg-example.cfg) for a more complex config
file that describes all features).

There are other borg wrappers too. For example, to check a repository:

    syborg check rsyncnet -- --verbose --progress --verify-data

To mount a repository:

    syborg mount rsyncnet /mnt

## installation

    sudo pip3 install syborg

To run as system service at specific times(recommended), copy
[syborg-backup@.service](syborg-backup@.service) and
[syborg-example@.timer](syborg-backup@.timer) to /etc/systemd/system and enable
for a specific backup job (which corresponds to a backup section in the config
file). For the above config file that would be: 

    systemctl edit syborg@example.timer   # add the [Timer]/OnCalendar section
    systemctl enable syborg@example.timer
    systemctl start syborg@example.timer

A simpler choice is to use crontab: 

    @hourly /usr/bin/syborg backup example

An option to get passphrases from the current user is to use
systemd-ask-password as `BORG_PASSCOMMAND` (see
[syborg-example.cfg](syborg-example.cfg)). In this case, you probably want to
install systemd-gtk-ask-password-agent to /usr/bin and
systemd-gtk-ask-password-agent.desktop to /etc/xdg/autostart. These files
implement a password agent and will ensure a gtk notification/dialog are
presented when syborg requires a password. 
