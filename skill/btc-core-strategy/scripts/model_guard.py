#!/usr/bin/env python3
"""Compatibility entry for the node-owned BTC deep-call identity gate."""
from model_daily import Blocked, claim, finish, launch_native, main, require_success, request, sha256, status

if __name__ == '__main__':
    raise SystemExit(main())
