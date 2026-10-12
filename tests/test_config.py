import dataclasses
import json
from pathlib import Path
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rsna_knee.config import AuditConfig, ConfigError, load_config


class ConfigurationTests(unittest.TestCase):
    def test_defaults_roundtrip_and_immutability(self):
        config = AuditConfig()
        self.assertEqual(config, AuditConfig.from_dict(config.to_dict()))
        self.assertFalse(config.inspect_csv)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.max_entries = 0

    def test_unknown_keys_and_wrong_object_rejected(self):
        for value in ({"token": "synthetic"}, [], None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                AuditConfig.from_dict(value)

    def test_limits_reject_bool_float_zero_and_excess(self):
        cases = ({"max_entries": True}, {"max_directories": 0}, {"max_depth": 5},
                 {"max_csv_rows": 1.5}, {"max_file_bytes": 10**12}, {"max_report_bytes": -1})
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ConfigError):
                AuditConfig.from_dict(case)

    def test_paths_and_flags_require_correct_types(self):
        for case in ({"input_base": ""}, {"output_dir": " padded "}, {"input_base": "bad\npath"},
                     {"data_root": 123}, {"inspect_csv": 1}):
            with self.subTest(case=case), self.assertRaises(ConfigError):
                AuditConfig.from_dict(case)

    def test_json_duplicate_keys_and_nonobjects_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            for content in ('{"max_depth": 1, "max_depth": 2}', '[]', '{not json}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ConfigError):
                    load_config(path)

    def test_config_file_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_bytes(b" " * (64 * 1024 + 1))
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_protected_config_rejected_before_read(self):
        for name in (".aws/config.json", "kaggle.json", ".env", "credentials/settings.json"):
            with self.subTest(name=name), patch.object(Path, "read_text", side_effect=AssertionError("Must not read")):
                with self.assertRaises(ConfigError):
                    load_config(name)

    def test_simulated_config_link_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text("{}", encoding="utf-8")
            original = Path.lstat
            def simulated(source, *args, **kwargs):
                if source == path:
                    return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
                return original(source, *args, **kwargs)
            with patch.object(Path, "lstat", simulated), patch.object(Path, "open", side_effect=AssertionError("Must not read linked config")):
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_checked_in_configuration_matches_schema(self):
        config = load_config(Path(__file__).resolve().parents[1] / "configs" / "audit.json")
        self.assertEqual(config, AuditConfig())
        self.assertEqual(json.loads(json.dumps(config.to_dict())), config.to_dict())
