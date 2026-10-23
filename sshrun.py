# -*- coding: utf-8 -*-
"""通过 SSH 在麒麟虚拟机上执行命令的小工具。
用法: python sshrun.py "命令"    （多个命令用 && 连接）
"""
from __future__ import annotations

import sys

import paramiko

HOST = "YOUR_SERVER_IP"
USER = "root"
PWD = "YOUR_PASSWORD"
PORT = 22


def run(cmd, timeout=600, capture=True):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PWD, timeout=30, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    client.close()
    return code, out, err


def main():
    if len(sys.argv) < 2:
        print("用法: python sshrun.py \"命令\"")
        sys.exit(1)
    cmd = sys.argv[1]
    code, out, err = run(cmd)
    if out.strip():
        print(out)
    if err.strip():
        print("[stderr]", err)
    print("[exit]", code)
    sys.exit(code)


if __name__ == "__main__":
    main()
