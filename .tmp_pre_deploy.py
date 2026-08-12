# -*- coding: utf-8 -*-
"""临时脚本（用完即删）：全项目搜 23-1 依据出处。"""
import time

import paramiko

SERVER, USER = '193.112.194.61', 'ubuntu'
PWD = 'j9Uq_BgeWsR^7*4U'


def run(cli, cmd, limit=6000):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=300)
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    print(f'$ {cmd[:130]}')
    if out.strip():
        print(out[-limit:])
    if err.strip():
        print('[stderr]', err[-800:])
    print('---')
    return out


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for i in range(6):
        try:
            cli.connect(SERVER, username=USER, password=PWD, timeout=30)
            break
        except Exception as e:
            print('连接重试', i, e)
            time.sleep(10)
    else:
        raise SystemExit('SSH 连接失败')

    run(cli, 'grep -rl "23-1" /home/ubuntu/reit-app/data/workspace/projects/ | head -10')
    run(cli, 'grep -rh "23-1 润泽" /home/ubuntu/reit-app/data/workspace/projects/ | head -5 | cut -c1-700')

    cli.close()


if __name__ == '__main__':
    main()
