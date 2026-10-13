#!/usr/bin/env python3
"""Install the platform as a macOS login service; no credentials in the plist."""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];label='com.tuyoo.spine-workbench';logs=root/'storage/service-logs';logs.mkdir(parents=True,exist_ok=True)
    env={'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')}
    codex=shutil.which('codex')
    if codex:env['SPINE_AGENT_CODEX']=codex
    node=shutil.which('node')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    if not node and bundled.exists():node=str(bundled)
    if node:env['SPINE_AGENT_NODE']=node
    data={'Label':label,'ProgramArguments':[sys.executable,str(root/'app/server.py'),'--host','0.0.0.0','--port','8780'],
          'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
          'EnvironmentVariables':env,'StandardOutPath':str(logs/'stdout.log'),'StandardErrorPath':str(logs/'stderr.log')}
    target=Path.home()/'Library/LaunchAgents'/f'{label}.plist' if a.install else a.output or root/'agent'/f'{label}.plist'
    target.parent.mkdir(parents=True,exist_ok=True)
    with open(target,'wb') as f:plistlib.dump(data,f)
    if a.install:
        domain=f'gui/{os.getuid()}';subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
        subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
    print(str(target))


if __name__=='__main__':main()
