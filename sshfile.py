# -*- coding: utf-8 -*-
"""通过 SFTP 上传/下载文件。用法: python sshfile.py put|get 本地路径 [远程路径]"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = "YOUR_SERVER_IP"
USER = "root"
PWD = "YOUR_PASSWORD"
PORT = 22
REMOTE_DIR = "/root/chaoxing_auto"


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PWD, timeout=30, look_for_keys=False, allow_agent=False)
    return c


def put(local, remote):
    c = connect()
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    c.close()
    print("上传完成:", remote)


def get(remote, local):
    c = connect()
    sftp = c.open_sftp()
    sftp.get(remote, local)
    sftp.close()
    c.close()
    print("下载完成:", local)


def upload_dir(local_dir, remote_dir):
    c = connect()
    sftp = c.open_sftp()
    try:
        sftp.mkdir(remote_dir)
    except Exception:
        pass
    for name in os.listdir(local_dir):
        if name in ("venv", "__pycache__", "progress.json", "config.json"):
            continue
        lp = os.path.join(local_dir, name)
        rp = remote_dir.rstrip("/") + "/" + name
        if os.path.isdir(lp):
            upload_dir(lp, rp)
        else:
            sftp.put(lp, rp)
            print("上传:", rp)
    sftp.close()
    c.close()


def main():
    if len(sys.argv) < 3:
        print("用法:\n  python sshfile.py put 本地文件 [远程路径]\n  python sshfile.py get 远程文件 本地路径\n  python sshfile.py dir 本地目录 [远程目录]")
        sys.exit(1)
    op = sys.argv[1]
    if op == "put":
        local = sys.argv[2]
        remote = sys.argv[3] if len(sys.argv) > 3 else REMOTE_DIR + "/" + os.path.basename(local)
        put(local, remote)
    elif op == "get":
        get(sys.argv[2], sys.argv[3])
    elif op == "dir":
        upload_dir(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else REMOTE_DIR)


if __name__ == "__main__":
    main()
