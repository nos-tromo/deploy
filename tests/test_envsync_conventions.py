"""The .env.example format checker (scripts/envsync/conventions.py and
scripts/check_env_examples.py): a compliant synthetic example, then one
minimal mutation per rule id."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Set, Tuple

from envsync_helpers import ROOT, assert_no_sentinel
from envsync.conventions import (
    MESSAGES,
    RULE,
    SECTIONS,
    Finding,
    check,
    check_compose,
    check_group_required,
    header,
    is_migrated,
    makefile_defaults,
    makefile_grep_keys,
)
from envsync.shared import load_table

TABLE = """\
audit-edge  edge-plane          AUDIT_EDGE_SECRET   gen
audit-edge  obs-plane           AUDIT_EDGE_SECRET   gen
edge-host   edge-plane          EDGE_HOST           source
edge-host   obs-plane           EDGE_HOST
edge-host   open-webui-service  EDGE_HOST
jwt         obs-plane           GRAFANA_JWT_SECRET  gen
jwt         vllm-service        AUDIT_JWT_SECRET    gen
"""
GROUPS = load_table(TABLE)

HEADER = """\
# obs-plane — example configuration
#
# deploy's `make env` writes .env from this file and keeps it in step
# (by hand: cp .env.example .env).
#
#   KEY=value     set per host
#   # KEY=value   optional; the value shown is the default
#
# Tags, on the line above a setting:
#   [required]          must be set on every host
#   [secret]            never printed; your value never follows the example
#   [shared: M [KEY]]   same value as member M (its KEY); make env fills it
#   [dev]               read only by the dev overlay (make up-dev)
#
# A synthetic member note: nothing here is a real host.
"""


def banner(title: str) -> str:
    return f"\n{RULE}\n# {title}\n{RULE}\n\n"


REQUIRED = (
    banner("Required")
    + """\
# The secret the audit relay checks, at least 32 characters.
# [required] [secret] [shared: edge-plane]
AUDIT_EDGE_SECRET=
"""
)
NETWORK = (
    banner("Network & proxy")
    + """\
# The external Docker network the scrapers join.
# DATA_NET=data-net
"""
)
PORT = """\
# The port the metrics store listens on.
# METRICS_PORT=9090
"""
URL = """\
# The address the dashboards query, built from the port above.
# METRICS_URL=http://metrics.example.invalid:${METRICS_PORT}
"""
MEMBER = (
    banner("obs-plane")
    + "# --- metrics ---\n\n"
    + PORT
    + URL
    + """\
# The title the dashboards show.
# DASHBOARD_TITLE="Synthetic federation"
# Behind a proxy the address differs:
#   METRICS_URL=http://proxy.example.invalid/metrics
"""
)
DEV = (
    banner("Development only")
    + """\
# The host port the dev overlay publishes.
# [dev]
# DASHBOARD_HOST_PORT=3000
"""
)
VALID = HEADER + REQUIRED + NETWORK + MEMBER + DEV

DIGEST = "busybox@sha256:" + "0" * 64

MAKE_DEFAULTS = {"INFRA_ROOT": "..", "APP_DIRS": "docint Nextext translator"}
MAKE_VALID = (
    "\n".join(header("deploy", "federation.env", "federation.env.example"))
    + "\n"
    + banner("deploy")
    + """\
# --- Members ---

# Where the member repos live.
# INFRA_ROOT=..
# The apps this host runs.
# APP_DIRS=docint Nextext translator
# How long a health gate waits, in seconds.
# WAIT_TIMEOUT=180
# 1 = rebuild every bundle.
# BUNDLE_FORCE=
"""
)

ALL_RULES = (
    [f"F{i}" for i in range(1, 5)]
    + [f"H{i}" for i in range(1, 4)]
    + [f"S{i}" for i in range(1, 6)]
    + [f"T{i}" for i in range(1, 7)]
    + [f"V{i}" for i in range(1, 12)]
    + [f"M{i}" for i in range(1, 6)]
    + ["X2", "X3", "X5"]
)

Pairs = Set[Tuple[str, Optional[str]]]


def mutate(text: str, old: str, new: str) -> str:
    """Replace the one occurrence of old; a test whose anchor drifts fails."""
    if text.count(old) != 1:
        raise AssertionError(f"anchor found {text.count(old)} times: {old!r}")
    return text.replace(old, new, 1)


def found(text: str, member: str = "obs-plane", **kwargs: object) -> Pairs:
    kwargs.setdefault("groups", GROUPS)
    findings = check(text, member, **kwargs)  # type: ignore[arg-type]
    return {(f.rule, f.key) for f in findings}


def lineno_of(text: str, raw: str) -> int:
    return text.split("\n").index(raw) + 1


class ValidTests(unittest.TestCase):
    def test_valid_example_is_clean(self) -> None:
        self.assertEqual(check(VALID, "obs-plane", groups=GROUPS), [])

    def test_valid_make_example_is_clean(self) -> None:
        self.assertEqual(
            check(MAKE_VALID, "deploy", syntax="make", make_defaults=MAKE_DEFAULTS),
            [],
        )

    def test_constants(self) -> None:
        self.assertEqual(RULE, "# " + "=" * 77)
        self.assertEqual(len(RULE), 79)
        self.assertEqual(
            SECTIONS,
            (
                "Required",
                "Auth & identity",
                "Network & proxy",
                "Inference",
                "<member>",
                "Audit & retention",
                "Development only",
            ),
        )

    def test_header_template(self) -> None:
        self.assertEqual(
            header("obs-plane", ".env", ".env.example"), HEADER.split("\n")[:13]
        )
        lines = header("deploy", "federation.env", "federation.env.example")
        self.assertEqual(lines[0], "# deploy — example configuration")
        self.assertEqual(
            lines[2],
            "# deploy's `make env` writes federation.env from this file and "
            "keeps it in step",
        )
        self.assertEqual(
            lines[3], "# (by hand: cp federation.env.example federation.env)."
        )
        self.assertTrue(all(len(line) <= 79 for line in lines))

    def test_is_migrated(self) -> None:
        self.assertTrue(is_migrated(VALID, "obs-plane"))
        self.assertFalse(is_migrated(VALID, "edge-plane"))
        self.assertFalse(is_migrated("# Edge gateway.\nEDGE_HOST=\n", "edge-plane"))
        self.assertFalse(is_migrated("", "edge-plane"))

    def test_messages_cover_every_rule(self) -> None:
        self.assertEqual(sorted(MESSAGES), sorted(ALL_RULES))
        for rule, message in MESSAGES.items():
            self.assertTrue(message, rule)

    def test_findings_sorted_by_line(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "#DATA_NET=<x>")
        text = mutate(text, "\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET=x\n")
        text = mutate(text, "# Network & proxy\n", "# Networking\n")
        lines = [f.lineno for f in check(text, "obs-plane", groups=GROUPS)]
        self.assertGreater(len(set(lines)), 2)
        self.assertEqual(lines, sorted(lines))

    def test_finding_shape(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "#DATA_NET=data-net")
        self.assertIn(
            Finding(lineno_of(text, "#DATA_NET=data-net"), "V3", "DATA_NET"),
            check(text, "obs-plane", groups=GROUPS),
        )


class FileRuleTests(unittest.TestCase):
    def test_f1_unparseable_line(self) -> None:
        text = mutate(
            VALID, "# DATA_NET=data-net\n", '# DATA_NET=data-net\nBROKEN="open\n'
        )
        self.assertIn(("F1", None), found(text))

    def test_f1_byte_order_mark_and_crlf(self) -> None:
        self.assertIn(("F1", None), found("\ufeff" + VALID))
        text = mutate(VALID, "# DATA_NET=data-net\n", "# DATA_NET=data-net\r\n")
        self.assertIn(("F1", "DATA_NET"), found(text))

    def test_f1_lint(self) -> None:
        text = mutate(VALID, "\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET= # note\n")
        self.assertIn(("F1", "AUDIT_EDGE_SECRET"), found(text))

    def test_f1_malformed_directive(self) -> None:
        text = mutate(
            VALID, "# --- metrics ---\n", "# --- metrics ---\n# renamed: OLD\n"
        )
        self.assertIn(("F1", None), found(text))

    def test_f2_leading_blank(self) -> None:
        text = mutate(
            VALID,
            "# The external Docker network",
            " # The external Docker network",
        )
        self.assertIn(("F2", None), found(text))

    def test_f2_tab_and_trailing_blank(self) -> None:
        text = mutate(VALID, "# The external Docker", "# The external\tDocker")
        self.assertIn(("F2", None), found(text))
        text = mutate(VALID, "# DATA_NET=data-net\n", "# DATA_NET=data-net \n")
        self.assertIn(("F2", "DATA_NET"), found(text))

    def test_f3_long_prose_line(self) -> None:
        ok = mutate(
            VALID, "# --- metrics ---\n", "# --- metrics ---\n# " + "a" * 77 + "\n"
        )
        self.assertEqual(check(ok, "obs-plane", groups=GROUPS), [])
        text = mutate(
            VALID, "# --- metrics ---\n", "# --- metrics ---\n# " + "a" * 78 + "\n"
        )
        self.assertIn(("F3", None), found(text))

    def test_f3_long_value_without_blank(self) -> None:
        text = mutate(
            VALID,
            "# --- metrics ---\n",
            "# --- metrics ---\n\n# The probe image, by digest.\n"
            f"# PROBE_PIN={DIGEST}\n# Or, pinned elsewhere:\n#   PROBE_PIN={DIGEST}\n",
        )
        self.assertGreater(len(f"# PROBE_PIN={DIGEST}"), 79)
        self.assertEqual(check(text, "obs-plane", groups=GROUPS), [])

    def test_f3_long_inline_comment(self) -> None:
        line = "# DATA_NET=data-net  # " + "the network the scrapers join " * 3
        self.assertGreater(len(line), 79)
        text = mutate(VALID, "# DATA_NET=data-net", line)
        self.assertIn(("F3", "DATA_NET"), found(text))
        text = mutate(VALID, "# DATA_NET=data-net", f"# DATA_NET={DIGEST}  # pin")
        self.assertIn(("F3", "DATA_NET"), found(text))

    def test_f3_long_value_with_blank(self) -> None:
        title = "# DASHBOARD_TITLE=" + '"' + "a b " * 20 + '"'
        text = mutate(VALID, '# DASHBOARD_TITLE="Synthetic federation"', title)
        self.assertIn(("F3", "DASHBOARD_TITLE"), found(text))

    def test_f4_lowercase_key(self) -> None:
        text = mutate(
            VALID, "# --- metrics ---\n", "# --- metrics ---\n# A note.\n# name=x\n"
        )
        self.assertIn(("F4", "name"), found(text))


class HeaderRuleTests(unittest.TestCase):
    def test_h1_changed_header_line(self) -> None:
        text = mutate(
            VALID, "# (by hand: cp .env.example .env).", "# (by hand: copy it)."
        )
        self.assertIn(("H1", None), found(text))

    def test_h1_wrong_env_names(self) -> None:
        self.assertIn(("H1", None), found(VALID, syntax="make"))

    def test_h2_setting_in_header(self) -> None:
        text = mutate(
            VALID,
            "# A synthetic member note: nothing here is a real host.\n",
            "# A synthetic member note: nothing here is a real host.\n"
            "# SCRAPE_INTERVAL=15s\n",
        )
        self.assertIn(("H2", "SCRAPE_INTERVAL"), found(text))

    def test_h3_legend_not_indented(self) -> None:
        text = mutate(
            VALID,
            "# A synthetic member note: nothing here is a real host.\n",
            "# [secret] values are never printed.\n",
        )
        self.assertIn(("H3", None), found(text))
        self.assertNotIn(("T1", None), found(text))


class SectionRuleTests(unittest.TestCase):
    def test_s1_unknown_title(self) -> None:
        text = mutate(VALID, "# Network & proxy\n", "# Networking\n")
        self.assertIn(("S1", None), found(text))

    def test_s1_other_member_title(self) -> None:
        text = mutate(VALID, "# obs-plane\n", "# edge-plane\n")
        self.assertIn(("S1", None), found(text))

    def test_s1_banner_not_closed(self) -> None:
        text = mutate(VALID, f"# Network & proxy\n{RULE}\n", "# Network & proxy\n")
        self.assertIn(("S1", None), found(text))

    def test_s2_swapped_sections(self) -> None:
        text = HEADER + NETWORK + REQUIRED + MEMBER + DEV
        self.assertIn(("S2", None), found(text))

    def test_s2_duplicate_section(self) -> None:
        text = HEADER + REQUIRED + NETWORK + banner("Network & proxy") + MEMBER + DEV
        self.assertIn(("S2", None), found(text))

    def test_s3_stray_rule(self) -> None:
        text = mutate(VALID, "# --- metrics ---\n", "# --- metrics ---\n# ==========\n")
        self.assertIn(("S3", None), found(text))

    def test_s4_prose_starting_with_dashes(self) -> None:
        for prose in (
            "# --no-diarize). The VAD guard skips silence.",
            "#   --enforce-eager",
        ):
            with self.subTest(prose=prose):
                text = mutate(
                    VALID, "# --- metrics ---\n", f"# --- metrics ---\n{prose}\n"
                )
                self.assertEqual(found(text), set())

    def test_s4_subsection_form(self) -> None:
        for bad in (
            "# -- metrics --",
            "# ---- metrics ------------",
            "# --- metrics (no closing dashes",
            "#--- metrics ---",
            "# ---",
        ):
            with self.subTest(bad=bad):
                text = mutate(VALID, "# --- metrics ---\n", bad + "\n")
                self.assertIn(("S4", None), found(text))

    def test_s5_section_is_nearest_banner_above(self) -> None:
        dev_key = (
            "# The host port the dev overlay publishes.\n"
            "# [dev]\n"
            "# DASHBOARD_HOST_PORT=3000\n"
        )
        # At the end of the file, the dev key is still under Development only.
        text = mutate(VALID, dev_key, "") + dev_key
        self.assertEqual(found(text), set())
        # Above that banner, it sits in the obs-plane section.
        moved = (
            HEADER + REQUIRED + NETWORK + MEMBER + dev_key + mutate(DEV, dev_key, "")
        )
        self.assertIn(("T6", "DASHBOARD_HOST_PORT"), found(moved))


class TagRuleTests(unittest.TestCase):
    def test_t1_tag_line_without_setting(self) -> None:
        text = mutate(VALID, "# [dev]\n# DASHBOARD", "# [dev]\n\n# DASHBOARD")
        self.assertIn(("T1", None), found(text))

    def test_t1_grammar(self) -> None:
        for bad in (
            "# [required][secret] [shared: edge-plane]",
            "# [required] [secret] [shared: edge-plane] the relay secret",
            "# [required] [secret] [shared:edge-plane]",
            "# [required] [secret] [shared]",
        ):
            with self.subTest(bad=bad):
                text = mutate(VALID, "# [required] [secret] [shared: edge-plane]", bad)
                self.assertIn(("T1", "AUDIT_EDGE_SECRET"), found(text))

    def test_t2_order_unknown_duplicate(self) -> None:
        for bad in (
            "# [secret] [required] [shared: edge-plane]",
            "# [required] [secret] [secret] [shared: edge-plane]",
            "# [required] [secret] [shared: edge-plane] [optional]",
        ):
            with self.subTest(bad=bad):
                text = mutate(VALID, "# [required] [secret] [shared: edge-plane]", bad)
                self.assertIn(("T2", "AUDIT_EDGE_SECRET"), found(text))

    def test_t3_active_outside_required(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net\n", "DATA_NET=\n")
        self.assertIn(("T3", "DATA_NET"), found(text))

    def test_t3_commented_inside_required(self) -> None:
        text = mutate(VALID, "\nAUDIT_EDGE_SECRET=\n", "\n# AUDIT_EDGE_SECRET=\n")
        self.assertIn(("T3", "AUDIT_EDGE_SECRET"), found(text))

    def test_t3_required_outside_required_section(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net\n", "# [required]\nDATA_NET=\n")
        self.assertIn(("T3", "DATA_NET"), found(text))

    def test_t3_active_without_required_tag(self) -> None:
        text = mutate(VALID, "# [required] [secret]", "# [secret]")
        self.assertIn(("T3", "AUDIT_EDGE_SECRET"), found(text))

    def test_t4_secret_tag(self) -> None:
        text = mutate(VALID, "# [required] [secret] [shared", "# [required] [shared")
        self.assertIn(("T4", "AUDIT_EDGE_SECRET"), found(text))
        text = mutate(VALID, "# DATA_NET=data-net", "# [secret]\n# DATA_NET=data-net")
        self.assertIn(("T4", "DATA_NET"), found(text))

    def test_t5_shared_missing_or_wrong(self) -> None:
        for bad in (
            "# [required] [secret]",
            "# [required] [secret] [shared: data-plane]",
            "# [required] [secret] [shared: edge-plane AUDIT_EDGE_SECRET]",
        ):
            with self.subTest(bad=bad):
                text = mutate(VALID, "# [required] [secret] [shared: edge-plane]", bad)
                self.assertIn(("T5", "AUDIT_EDGE_SECRET"), found(text))

    def test_t5_shared_without_row(self) -> None:
        text = mutate(
            VALID, "# DATA_NET=data-net", "# [shared: edge-plane]\n# DATA_NET=data-net"
        )
        self.assertIn(("T5", "DATA_NET"), found(text))

    def test_t5_non_source_names_only_the_source(self) -> None:
        host = "# [required] [shared: edge-plane]\nEDGE_HOST=\n"
        text = mutate(VALID, "\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET=\n" + host)
        self.assertEqual(found(text), set())
        wide = host.replace("edge-plane]", "edge-plane, open-webui-service]")
        text = mutate(VALID, "\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET=\n" + wide)
        self.assertIn(("T5", "EDGE_HOST"), found(text))

    def test_t5_source_names_all_others_in_table_order(self) -> None:
        edge = HEADER.replace("# obs-plane —", "# edge-plane —")
        edge += banner("Required")
        good = edge + (
            "# [required] [shared: obs-plane, open-webui-service]\nEDGE_HOST=\n"
            "# [required] [secret] [shared: obs-plane]\nAUDIT_EDGE_SECRET=\n"
        )
        self.assertEqual(check(good, "edge-plane", groups=GROUPS), [])
        swapped = good.replace(
            "obs-plane, open-webui-service", "open-webui-service, obs-plane"
        )
        self.assertIn(("T5", "EDGE_HOST"), found(swapped, "edge-plane"))

    def test_t5_partner_key_named_when_it_differs(self) -> None:
        jwt = (
            "# [secret] [shared: vllm-service AUDIT_JWT_SECRET]\n"
            "# GRAFANA_JWT_SECRET=\n"
        )
        text = mutate(VALID, "# --- metrics ---\n\n", "# --- metrics ---\n\n" + jwt)
        self.assertEqual(found(text), set())
        short = jwt.replace(" AUDIT_JWT_SECRET]", "]")
        text = mutate(VALID, "# --- metrics ---\n\n", "# --- metrics ---\n\n" + short)
        self.assertIn(("T5", "GRAFANA_JWT_SECRET"), found(text))

    def test_t6_dev_tag(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "# [dev]\n# DATA_NET=data-net")
        self.assertIn(("T6", "DATA_NET"), found(text))
        text = mutate(VALID, "# [dev]\n", "")
        self.assertIn(("T6", "DASHBOARD_HOST_PORT"), found(text))


class ValueRuleTests(unittest.TestCase):
    def test_v1_active_value(self) -> None:
        text = mutate(VALID, "\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET=x\n")
        self.assertIn(("V1", "AUDIT_EDGE_SECRET"), found(text))

    def test_v2_two_commented_lines(self) -> None:
        text = mutate(
            VALID,
            "# DATA_NET=data-net\n",
            "# DATA_NET=data-net\n# DATA_NET=other-net\n",
        )
        self.assertIn(("V2", "DATA_NET"), found(text))

    def test_v2_commented_line_for_active_key(self) -> None:
        text = mutate(
            VALID,
            "# DATA_NET=data-net\n",
            "# DATA_NET=data-net\n# AUDIT_EDGE_SECRET=\n",
        )
        self.assertIn(("V2", "AUDIT_EDGE_SECRET"), found(text))

    def test_v3_no_blank_after_hash(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "#DATA_NET=data-net")
        self.assertIn(("V3", "DATA_NET"), found(text))

    def test_v4_placeholder(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "# DATA_NET=<x>")
        self.assertIn(("V4", "DATA_NET"), found(text))

    def test_v5_self_reference(self) -> None:
        text = mutate(VALID, "# METRICS_PORT=9090", "# METRICS_PORT=${METRICS_PORT}")
        self.assertIn(("V5", "METRICS_PORT"), found(text))

    def test_v6_secret_with_value(self) -> None:
        text = mutate(
            VALID,
            "# --- metrics ---\n\n",
            "# --- metrics ---\n\n# [secret]\n# SOME_SECRET=x\n",
        )
        self.assertIn(("V6", "SOME_SECRET"), found(text))

    def test_v7_comment_after_empty_value(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "# DATA_NET=   # c")
        self.assertIn(("V7", "DATA_NET"), found(text))

    def test_v8_tab_before_comment(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "# DATA_NET=data-net\t# c")
        self.assertIn(("V8", "DATA_NET"), found(text))
        ok = mutate(VALID, "# DATA_NET=data-net", "# DATA_NET=data-net  # c")
        self.assertEqual(found(ok), set())

    def test_v9_unquoted_prose_value(self) -> None:
        text = mutate(
            VALID,
            '# DASHBOARD_TITLE="Synthetic federation"',
            "# DASHBOARD_TITLE=Synthetic federation",
        )
        self.assertIn(("V9", "DASHBOARD_TITLE"), found(text))
        text = mutate(
            VALID,
            '# DASHBOARD_TITLE="Synthetic federation"',
            "# DASHBOARD_TITLE=Synthetic.",
        )
        self.assertIn(("V9", "DASHBOARD_TITLE"), found(text))

    def test_v9_dotenv_only(self) -> None:
        pairs = found(MAKE_VALID, "deploy", syntax="make", make_defaults=MAKE_DEFAULTS)
        self.assertNotIn(("V9", "APP_DIRS"), pairs)

    def test_v10_reference_above_target(self) -> None:
        text = mutate(VALID, PORT + URL, URL + PORT)
        self.assertIn(("V10", "METRICS_URL"), found(text))

    def test_v11_comment_on_grepped_key(self) -> None:
        text = mutate(
            VALID, "# DATA_NET=data-net", "# DATA_NET=data-net  # the scrape network"
        )
        self.assertIn(
            ("V11", "DATA_NET"), found(text, grep_keys=frozenset({"DATA_NET"}))
        )
        self.assertEqual(found(text), set())
        self.assertEqual(found(VALID, grep_keys=frozenset({"DATA_NET"})), set())


class MakeRuleTests(unittest.TestCase):
    def pairs(self, text: str) -> Pairs:
        return found(text, "deploy", syntax="make", make_defaults=MAKE_DEFAULTS)

    def test_m1_inline_comment(self) -> None:
        text = mutate(MAKE_VALID, "# INFRA_ROOT=..", "# INFRA_ROOT=.. # the workspace")
        self.assertIn(("M1", "INFRA_ROOT"), self.pairs(text))

    def test_m2_blanks_around_equals(self) -> None:
        for bad in ("INFRA_ROOT = ..", "# INFRA_ROOT = ..", "# INFRA_ROOT= .."):
            with self.subTest(bad=bad):
                text = mutate(MAKE_VALID, "# INFRA_ROOT=..", bad)
                self.assertIn(("M2", "INFRA_ROOT"), self.pairs(text))

    def test_m3_active_line(self) -> None:
        text = mutate(MAKE_VALID, "# INFRA_ROOT=..", "INFRA_ROOT=..")
        self.assertIn(("M3", "INFRA_ROOT"), self.pairs(text))

    def test_m4_value_differs_from_default(self) -> None:
        for old, new, key in (
            ("# INFRA_ROOT=..", "# INFRA_ROOT=../x", "INFRA_ROOT"),
            ("# BUNDLE_FORCE=", "# BUNDLE_FORCE=1", "BUNDLE_FORCE"),
            ("# WAIT_TIMEOUT=180", "# WAIT_TIMEOUT=60", "WAIT_TIMEOUT"),
        ):
            with self.subTest(key=key):
                self.assertIn(("M4", key), self.pairs(mutate(MAKE_VALID, old, new)))

    def test_m5_only_the_deploy_section(self) -> None:
        text = mutate(MAKE_VALID, "# deploy\n", "# Inference\n")
        self.assertIn(("M5", None), self.pairs(text))
        self.assertNotIn(("M5", None), self.pairs(MAKE_VALID))

    def test_m_rules_only_for_make(self) -> None:
        pairs = found(VALID, make_defaults={})
        self.assertFalse([rule for rule, _ in pairs if rule.startswith("M")])


COMPOSE = """\
services:
  metrics:
    environment:
      - DATA_NET=${DATA_NET:-data-net}
      - PORT=${METRICS_PORT-9090}
      - SECRET=${AUDIT_EDGE_SECRET:?set it}
      - ESCAPED=$${NOT_A_VARIABLE}
      - IMAGE=x:${OBS_PLANE_VERSION}
"""
OVERRIDE = """\
services:
  metrics:
    ports:
      - "${DASHBOARD_HOST_PORT:-3000}:3000"
"""


def compose_pairs(
    compose: str, override: Optional[str] = OVERRIDE, text: str = VALID
) -> Pairs:
    return {(f.rule, f.key) for f in check_compose(text, compose, override)}


class ComposeRuleTests(unittest.TestCase):
    def test_clean(self) -> None:
        self.assertEqual(check_compose(VALID, COMPOSE, OVERRIDE), [])
        self.assertEqual(check_compose(VALID, COMPOSE, None), [])

    def test_compose_default_mismatch(self) -> None:
        for old, new, key in (
            ("${DATA_NET:-data-net}", "${DATA_NET:-other-net}", "DATA_NET"),
            ("${METRICS_PORT-9090}", "${METRICS_PORT-9091}", "METRICS_PORT"),
        ):
            with self.subTest(key=key):
                pairs = compose_pairs(mutate(COMPOSE, old, new))
                self.assertEqual(pairs, {("X3", key)})

    def test_compose_default_mismatch_line_and_no_value(self) -> None:
        got = check_compose(VALID, mutate(COMPOSE, "data-net}", "secret-net}"), OVERRIDE)
        self.assertEqual(
            [(f.lineno, f.rule, f.key) for f in got],
            [(lineno_of(VALID, "# DATA_NET=data-net"), "X3", "DATA_NET")],
        )

    def test_nested_default_is_not_compared(self) -> None:
        text = mutate(COMPOSE, "${DATA_NET:-data-net}", "${DATA_NET:-${OTHER:-x}}")
        self.assertNotIn(("X3", "DATA_NET"), compose_pairs(text))

    def test_prose_only_key_is_not_compared(self) -> None:
        text = mutate(
            VALID,
            "#   METRICS_URL=http://proxy.example.invalid/metrics\n",
            "#   METRICS_URL=http://proxy.example.invalid/metrics\n"
            "#   SHAPE_ONLY=a\n",
        )
        extra = "      - S=${SHAPE_ONLY:-b}\n"
        pairs = compose_pairs(COMPOSE + extra, text=text)
        self.assertNotIn(("X3", "SHAPE_ONLY"), pairs)

    def test_dev_key_is_compared_to_the_override(self) -> None:
        pairs = compose_pairs(COMPOSE, mutate(OVERRIDE, "-3000}", "-3001}"))
        self.assertEqual(pairs, {("X3", "DASHBOARD_HOST_PORT")})
        base = COMPOSE + "      - P=${DASHBOARD_HOST_PORT:-3001}\n"
        self.assertEqual(compose_pairs(base), set())

    def test_required_key_from_colon_question(self) -> None:
        self.assertEqual(
            compose_pairs(mutate(COMPOSE, "${DATA_NET:-data-net}", "${DATA_NET:?x}")),
            {("X3", "DATA_NET")},
        )
        self.assertNotIn(("X3", "AUDIT_EDGE_SECRET"), compose_pairs(COMPOSE))
        text = mutate(
            VALID,
            "# [required] [secret] [shared: edge-plane]\nAUDIT_EDGE_SECRET=\n",
            "# [secret] [shared: edge-plane]\n# AUDIT_EDGE_SECRET=\n",
        )
        self.assertIn(("X3", "AUDIT_EDGE_SECRET"), compose_pairs(COMPOSE, text=text))

    def test_undocumented_compose_variable(self) -> None:
        for ref in ("${UNDOCUMENTED}", "$UNDOCUMENTED", "${UNDOCUMENTED:-1}"):
            with self.subTest(ref=ref):
                pairs = compose_pairs(COMPOSE + f"      - V={ref}\n")
                self.assertEqual(pairs, {("X5", "UNDOCUMENTED")})
        self.assertEqual(
            compose_pairs(COMPOSE, OVERRIDE + "      - V=${UNDOCUMENTED}\n"),
            {("X5", "UNDOCUMENTED")},
        )

    def test_version_and_escape_are_ignored(self) -> None:
        pairs = compose_pairs(COMPOSE)
        self.assertNotIn(("X5", "OBS_PLANE_VERSION"), pairs)
        self.assertNotIn(("X5", "NOT_A_VARIABLE"), pairs)


def sibling(member: str, body: str) -> str:
    return "\n".join(header(member, ".env", ".env.example")) + "\n" + body


class GroupRequiredTests(unittest.TestCase):
    REQUIRED = "# [required] [secret] [shared: obs-plane]\nAUDIT_EDGE_SECRET=\n"

    def test_group_required_disagrees(self) -> None:
        got = check_group_required(
            {
                "edge-plane": sibling("edge-plane", self.REQUIRED),
                "obs-plane": sibling("obs-plane", "# AUDIT_EDGE_SECRET=\n"),
            },
            GROUPS,
        )
        self.assertEqual(
            [(f.lineno, f.rule, f.key) for f in got], [(len(header("obs-plane", "", "")) + 1, "X2", "AUDIT_EDGE_SECRET")]
        )
        agree = {m: sibling(m, self.REQUIRED) for m in ("edge-plane", "obs-plane")}
        self.assertEqual(check_group_required(agree, GROUPS), [])

    def test_unmigrated_and_absent_members_are_skipped(self) -> None:
        required = sibling("edge-plane", self.REQUIRED)
        self.assertEqual(
            check_group_required(
                {"edge-plane": required, "obs-plane": "AUDIT_EDGE_SECRET=\n"}, GROUPS
            ),
            [],
        )
        self.assertEqual(check_group_required({"edge-plane": required}, GROUPS), [])


MAKEFILE = (
    "PROFILE ?= $(or $(strip $(shell test -f .env && grep -E '^PROFILE=' .env"
    " | cut -d= -f2)),cpu)\n"
    "DATA_NET      ?= $(or $(strip $(shell test -f .env && grep -E '^DATA_NET='"
    " .env | cut -d= -f2)),data-net)\n"
    "INFRA_ROOT   ?= ..\n"
    "WAIT_PROBE_IMAGE ?= busybox:1.37  \n"
    "APP_DIRS     ?= docint Nextext translator\n"
    "OTHER := x\n"
    "\n"
    "check:\n"
    "\t@grep -Eq '^COMPOSE_PROFILES=.*gpu' .env || true\n"
    "\t@grep -o 'docker\\.io/grafana/alloy:[^\"]*' docker/compose.yaml\n"
)


class MakefileHelperTests(unittest.TestCase):
    def test_makefile_helpers(self) -> None:
        self.assertEqual(
            makefile_grep_keys(MAKEFILE),
            frozenset({"PROFILE", "DATA_NET", "COMPOSE_PROFILES"}),
        )
        self.assertEqual(
            makefile_defaults(MAKEFILE),
            {
                "PROFILE": "$(or $(strip $(shell test -f .env && grep -E "
                "'^PROFILE=' .env | cut -d= -f2)),cpu)",
                "DATA_NET": "$(or $(strip $(shell test -f .env && grep -E "
                "'^DATA_NET=' .env | cut -d= -f2)),data-net)",
                "INFRA_ROOT": "..",
                "WAIT_PROBE_IMAGE": "busybox:1.37",
                "APP_DIRS": "docint Nextext translator",
            },
        )
        self.assertEqual(makefile_grep_keys(""), frozenset())
        self.assertEqual(makefile_defaults(""), {})


SENTINEL_LINES = (
    ("\nAUDIT_EDGE_SECRET=\n", "\nAUDIT_EDGE_SECRET=SENTINEL-one # SENTINEL-two\n"),
    ("# DATA_NET=data-net", "# DATA_NET=<SENTINEL-three>\t# SENTINEL-four"),
    ("# METRICS_PORT=9090", "# METRICS_PORT=SENTINEL-five ${METRICS_PORT}"),
    (
        "# --- metrics ---\n",
        '# --- metrics ---\nBROKEN="SENTINEL-six\n# name=SENTINEL-7\n',
    ),
)


def sentinel_text() -> str:
    text = VALID
    for old, new in SENTINEL_LINES:
        text = mutate(text, old, new)
    return text


def run_check(
    *args: object, table: Optional[Path] = None
) -> subprocess.CompletedProcess:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "check_env_examples.py")]
    if table is not None:
        command += ["--table", str(table)]
    command += [str(arg) for arg in args]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=60, check=False
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.table = self.tmp / "env-sync.conf"
        self.table.write_text(TABLE, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def member(self, name: str, example: str, makefile: Optional[str] = None) -> Path:
        folder = self.tmp / name
        folder.mkdir()
        (folder / ".env.example").write_text(example, encoding="utf-8")
        if makefile is not None:
            (folder / "Makefile").write_text(makefile, encoding="utf-8")
        return folder

    def deploy(self, example: str, makefile: str) -> Path:
        folder = self.tmp / "deploy"
        folder.mkdir()
        (folder / "federation.env.example").write_text(example, encoding="utf-8")
        (folder / "Makefile").write_text(makefile, encoding="utf-8")
        return folder

    def test_clean_member(self) -> None:
        done = run_check(self.member("obs-plane", VALID), table=self.table)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_reports_findings(self) -> None:
        text = mutate(VALID, "# DATA_NET=data-net", "#DATA_NET=data-net")
        done = run_check(self.member("obs-plane", text), table=self.table)
        self.assertEqual(done.returncode, 1)
        lineno = lineno_of(text, "#DATA_NET=data-net")
        self.assertEqual(
            done.stdout,
            f"obs-plane/.env.example:{lineno}: V3 DATA_NET {MESSAGES['V3']}\n",
        )

    def test_key_dash_when_no_key(self) -> None:
        text = mutate(VALID, "# Network & proxy\n", "# Networking\n")
        done = run_check(self.member("obs-plane", text), table=self.table)
        lineno = lineno_of(text, "# Networking")
        self.assertIn(
            f"obs-plane/.env.example:{lineno}: S1 - {MESSAGES['S1']}\n", done.stdout
        )

    def test_member_from_directory_name(self) -> None:
        done = run_check(self.member("edge-plane", VALID), table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertIn(" H1 - ", done.stdout)

    def test_grep_keys_from_makefile(self) -> None:
        text = mutate(
            VALID, "# DATA_NET=data-net", "# DATA_NET=data-net  # the network"
        )
        makefile = "DATA_NET ?= $(shell grep -E '^DATA_NET=' .env | cut -d= -f2)\n"
        done = run_check(self.member("obs-plane", text, makefile), table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertIn(" V11 DATA_NET ", done.stdout)

    def test_make_syntax_for_deploy(self) -> None:
        makefile = "INFRA_ROOT ?= ..\nAPP_DIRS ?= docint Nextext translator\n"
        folder = self.deploy(MAKE_VALID, makefile)
        done = run_check(folder, table=self.table)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        (folder / "Makefile").write_text("INFRA_ROOT ?= ../other\n", encoding="utf-8")
        done = run_check(folder, table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertIn("deploy/federation.env.example:", done.stdout)
        self.assertIn(" M4 INFRA_ROOT ", done.stdout)
        self.assertIn(" M4 APP_DIRS ", done.stdout)

    def test_compose_checks_for_a_member_with_compose(self) -> None:
        folder = self.member("obs-plane", VALID)
        (folder / "docker").mkdir()
        (folder / "docker" / "compose.yaml").write_text(COMPOSE, encoding="utf-8")
        (folder / "docker" / "compose.override.yaml").write_text(
            OVERRIDE, encoding="utf-8"
        )
        done = run_check(folder, table=self.table)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        (folder / "docker" / "compose.yaml").write_text(
            mutate(COMPOSE, "data-net}", "other-net}"), encoding="utf-8"
        )
        done = run_check(folder, table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertIn(" X3 DATA_NET ", done.stdout)

    def test_group_required_across_dirs(self) -> None:
        edge = self.member("edge-plane", sibling("edge-plane", GroupRequiredTests.REQUIRED))
        obs = self.member("obs-plane", sibling("obs-plane", "# AUDIT_EDGE_SECRET=\n"))
        done = run_check(edge, obs, table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertIn("obs-plane/.env.example:", done.stdout)
        self.assertIn(" X2 AUDIT_EDGE_SECRET ", done.stdout)

    def test_several_dirs(self) -> None:
        good = self.member("obs-plane", VALID)
        bad = self.member("edge-plane", VALID)
        done = run_check(good, bad, table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            all(line.startswith("edge-plane/") for line in done.stdout.splitlines())
        )

    def test_not_utf8(self) -> None:
        folder = self.member("obs-plane", VALID)
        data = VALID.encode("utf-8").replace(b"data-net\n", b"data-\xffnet\n", 1)
        (folder / ".env.example").write_bytes(data)
        done = run_check(folder, table=self.table)
        self.assertEqual(done.returncode, 1)
        lineno = lineno_of(VALID, "# DATA_NET=data-net")
        self.assertEqual(done.stdout.count(f".env.example:{lineno}: F1 "), 1)

    def test_usage_errors(self) -> None:
        self.assertEqual(run_check(table=self.table).returncode, 2)
        missing = self.tmp / "translator"
        missing.mkdir()
        done = run_check(missing, table=self.table)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        done = run_check(self.member("obs-plane", VALID), table=self.tmp / "nope.conf")
        self.assertEqual(done.returncode, 2)
        (self.tmp / "bad.conf").write_text(
            "one-row  obs-plane  DATA_NET\n", encoding="utf-8"
        )
        done = run_check(self.tmp / "obs-plane", table=self.tmp / "bad.conf")
        self.assertEqual(done.returncode, 2)

    def test_findings_carry_no_values(self) -> None:
        text = sentinel_text()
        findings = check(text, "obs-plane", groups=GROUPS)
        rules = {f.rule for f in findings}
        self.assertTrue({"F1", "V1", "V4", "V6", "V8", "F4"} <= rules, rules)
        assert_no_sentinel(self, repr(findings))
        done = run_check(self.member("obs-plane", text), table=self.table)
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stdout)
        assert_no_sentinel(self, done.stdout, done.stderr)
        for message in MESSAGES.values():
            assert_no_sentinel(self, message)


if __name__ == "__main__":
    unittest.main()
