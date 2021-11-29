import os
from setuptools import setup


VERSION = '1.0.0'
REPO    = 'https://github.com/tarruda/syborg'


setup(
    name='syborg',
    version=VERSION,
    description='Nice borgbackup wrapper',
    py_modules=['syborg', 'password_agent'],
    data_files=[('share/syborg', [
        'syborg-example.cfg',
        'syborg-backup@.service',
        'syborg-backup@.timer',
        'systemd-gtk-ask-password-agent.desktop',
        ])],
    author='Thiago de Arruda Padilha',
    author_email='tpadilha84@gmail.com',
    url=REPO,
    download_url='{0}/archive/{1}.tar.gz'.format(REPO, VERSION),
    license='MIT',
    install_requires=['ush', 'pyxdg', 'pexpect'],
    entry_points='''
    [console_scripts]
    syborg=syborg:main
    systemd-gtk-ask-password-agent=password_agent:main
    ''',
    )
