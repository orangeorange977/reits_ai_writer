# -*- coding: utf-8 -*-
"""临时发版脚本（用完即删）：weak 红框修复上线。"""
import time

import paramiko

SERVER, USER = '193.112.194.61', 'ubuntu'
PWD = 'j9Uq_BgeWsR^7*4U'
LOCAL_TAR = '/tmp/reit_deploy.tar.gz'
SUFFIX = time.strftime('%m%d%H%M')


def run(cli, cmd, show=True, limit=4000, timeout=900):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    if show:
        print(f'$ {cmd[:150]}')
        if out.strip():
            print(out[-limit:])
        if err.strip():
            print('[stderr]', err[-1500:])
        print('---')
    return out, err


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

    # 1. 活跃连接检查：最多等 3 分钟（容器内 ESTABLISHED 为 0 才可安全部署）
    chk = 'awk "NR>1 && \\$4==\\"01\\"" /proc/net/tcp /proc/net/tcp6 | wc -l'
    for _ in range(18):
        out, _ = run(cli, f'echo "{PWD}" | sudo -S docker exec reit-app-app-1 sh -c \'{chk}\'', show=False)
        n = int(out.strip() or 0)
        print('活跃连接:', n)
        if n == 0:
            break
        time.sleep(10)

    # 2. 备份
    run(cli, f'cd /home/ubuntu && cp -a REIT-AI-System REIT-AI-System.bak.{SUFFIX} && ls -d REIT-AI-System.bak.* | tail -3')

    # 3. 上传
    sftp = cli.open_sftp()
    for i in range(3):
        try:
            sftp.put(LOCAL_TAR, '/tmp/reit_deploy.tar.gz')
            print('上传完成')
            break
        except Exception as e:
            print('上传重试', i, e)
            time.sleep(5)
    else:
        raise SystemExit('上传失败')
    sftp.close()

    # 4. 解压 + rsync（三排除 + data 保护）+ 重建
    cmd = (
        'rm -rf /tmp/reit-deploy && mkdir -p /tmp/reit-deploy && '
        'tar -xzf /tmp/reit_deploy.tar.gz -C /tmp/reit-deploy && '
        f'echo {PWD} | sudo -S rsync -a --delete '
        '--exclude=workspace --exclude=.env --exclude=docker-compose.yml --exclude=data '
        '/tmp/reit-deploy/ /home/ubuntu/REIT-AI-System/ && '
        f'cd /home/ubuntu/REIT-AI-System && echo {PWD} | sudo -S '
        'docker compose -p reit-app up -d --build'
    )
    run(cli, cmd, timeout=900)

    # 5. 健康检查：每 10s 一次，最多 2 分钟
    ok = False
    for _ in range(12):
        out, _ = run(cli, 'curl -s -o /dev/null -w "%{http_code}" http://localhost/health', show=False)
        print('health:', out.strip())
        if out.strip() == '200':
            ok = True
            break
        time.sleep(10)
    if not ok:
        run(cli, f'echo {PWD} | sudo -S docker logs reit-app-app-1 --tail 40')
        raise SystemExit('健康检查未通过')

    # 6. 前端新代码验证
    run(cli, 'curl -s http://localhost/js/app.js | grep -c "并框出最相关段落"')

    cli.close()
    print('部署完成')


if __name__ == '__main__':
    main()
