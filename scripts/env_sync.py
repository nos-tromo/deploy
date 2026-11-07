#!/usr/bin/env python3
"""make env / make env-check: bring each .env in step with its .env.example."""

from __future__ import annotations

import sys

import envsync.cli

sys.exit(envsync.cli.main(sys.argv[1:]))
