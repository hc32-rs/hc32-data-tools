"""Contract tests for the proposal exporter (no PAC generation required)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import export_peripheral_versions as export


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for directory in ('docs', 'data/csv', 'data/registers', 'svd'):
            (self.repo / directory).mkdir(parents=True)
        (self.repo / 'data/csv/register_versions.csv').write_text(
            'series,instance,kind,version,block\n'
            'HC32TEST,I2C0,i,v1,I\nHC32TEST,I2S0,i,v2,I\n')
        (self.repo / 'svd/HC32TEST.svd').write_text(
            '<device xmlns="urn:svd"><peripherals>'
            '<peripheral><name>I2C0</name></peripheral>'
            '<peripheral><name>I2S0</name></peripheral>'
            '</peripherals></device>')
        layouts = []
        for number, kind, instance in [(1, 'i2c', 'I2C0'), (2, 'i2s', 'I2S0')]:
            text = f'block/I:\n  items:\n  - name: CR\n    byte_offset: {number * 4}\n'
            (self.repo / f'data/registers/i_v{number}.yaml').write_text(text)
            layouts.append(dict(source=f'i_v{number}', source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                kind=kind, version='v1', status='proposed', tier=1,
                                topic='spi-i2c-i2s', rationale='独立接口', register_count=1, field_count=0,
                                instances={'HC32TEST': [instance]}))
        self.proposal = dict(schema_version=1, side='ddl', stage='proposal-awaiting-confirmation', layouts=layouts)
        self.save()

    def save(self):
        (self.repo / 'docs/peripheral-version-map.json').write_text(json.dumps(self.proposal))

    def load(self):
        return export.load_review(self.repo, 'ddl')

    def test_truncated_source_kind_has_distinct_complete_target_names(self):
        _, rows = self.load()
        self.assertEqual([r['kind'] for r in rows], ['i', 'i'])
        self.assertEqual({r['target_file'] for r in rows}, {'i2c_ddl_v1.rs', 'i2s_ddl_v1.rs'})
        self.assertEqual({r['instance'] for r in rows}, {'I2C0', 'I2S0'})
        self.assertEqual(export.csv_text(rows), export.csv_text(self.load()[1]))

    def test_changed_source_requires_review(self):
        with (self.repo / 'data/registers/i_v1.yaml').open('a') as stream:
            stream.write('# source changed\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            self.load()

    def test_missing_instance_is_not_silently_dropped(self):
        self.proposal['layouts'].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, 'unassigned instances'):
            self.load()

    def test_duplicate_target_is_rejected(self):
        self.proposal['layouts'][1]['kind'] = 'i2c'
        self.save()
        with self.assertRaisesRegex(ValueError, 'duplicate target'):
            self.load()

    def test_duplicate_instance_is_rejected(self):
        self.proposal['layouts'][1]['instances']['HC32TEST'] = ['I2C0']
        self.save()
        with self.assertRaisesRegex(ValueError, 'duplicate assignment'):
            self.load()

    def test_source_mapping_drift_is_rejected(self):
        self.proposal['layouts'][0]['instances']['HC32TEST'] = ['I2S0']
        self.save()
        with self.assertRaisesRegex(ValueError, 'source mapping changed'):
            self.load()

    def test_non_numeric_or_skipped_variant_is_rejected(self):
        for version, message in [('v1a', 'invalid version'), ('v1_0', 'invalid version'),
                                 ('v1_2', 'non-contiguous variants')]:
            with self.subTest(version=version):
                self.proposal['layouts'][0]['version'] = version
                self.save()
                with self.assertRaisesRegex(ValueError, message):
                    self.load()

    def test_incorrect_review_counts_are_rejected(self):
        self.proposal['layouts'][0]['register_count'] = 99
        self.save()
        with self.assertRaisesRegex(ValueError, 'counts disagree'):
            self.load()

    def test_exclusion_scope_is_instance_aware(self):
        for instance in ('TMR61', 'TMR610', 'TMR6_COMMON', 'USBFS', 'USBHS', 'HRPWM_COMMON', 'SDIOC2'):
            with self.subTest(instance=instance):
                self.assertTrue(export.is_excluded('ll', 'HC32F4A8', instance))
                self.assertFalse(export.is_excluded('ddl', 'HC32F4A8', instance))
        for instance in ('TMR0', 'TMR01', 'TMR21', 'TMR4_ECER', 'EFM2', 'PERIC', 'PKE'):
            self.assertFalse(export.is_excluded('ll', 'HC32M458', instance))
        self.assertTrue(export.is_excluded('ll', 'HC32M458', 'DFM'))
        self.assertFalse(export.is_excluded('ll', 'HC32F175', 'DFM'))

    def test_unapproved_exclusion_is_rejected(self):
        self.proposal['layouts'][0].update(status='excluded', tier=0, topic=None, version='')
        self.save()
        with self.assertRaisesRegex(ValueError, 'exclusion scope mismatch'):
            self.load()

    def test_marker_update_preserves_surrounding_evidence(self):
        before = 'SDK evidence\n' + export.BEGIN + '\nold table\n' + export.END + '\nIRQ evidence\n'
        updated = export.replace_block(before, 'new table')
        self.assertEqual(updated, before.replace('old table', 'new table'))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            export.replace_block(before + export.BEGIN, 'bad')

    def test_field_and_access_differences_are_visible(self):
        base = {'block/I': {'items': [{'name': 'CR', 'byte_offset': 0, 'fieldset': 'CR'}]},
                'fieldset/CR': {'fields': [{'name': 'EN', 'bit_offset': 0, 'bit_size': 1}]}}
        current = json.loads(json.dumps(base))
        current['fieldset/CR']['fields'][0]['bit_offset'] = 1
        self.assertIn('定义差异 CR', export.difference_note(current, base, 'i2c_ddl_v1'))


if __name__ == '__main__':
    unittest.main()
