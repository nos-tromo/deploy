#!/usr/bin/env python3
"""Check each directory's .env.example (deploy's federation.env.example)
against the one format: python3 -B check_env_examples.py [--table FILE] DIR..."""

from __future__ import annotations

import sys

import envsync.conventions

sys.exit(envsync.conventions.main(sys.argv[1:]))
