#!/usr/bin/env python3
"""Build finite local PUBLIC reading views; no node or market request is made."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--base-read-set', type=Path, required=True)
    parser.add_argument('--supplemental', type=Path, action='append', default=[])
    parser.add_argument('--reader-scripts', type=Path, default=Path(__file__).parent,
                        help='Explicit installed reader implementation; recorded independently of this helper code.')
    args = parser.parse_args()
    sys.path.insert(0, str(args.reader_scripts.resolve()))
    from v4 import material_reads
    module_path = Path(__file__).parent / 'v4' / 'continuous_reading.py'
    spec = importlib.util.spec_from_file_location('btc_public_reading_repair', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.build(args.run_directory, args.snapshot, args.native, args.base_read_set, material_reads,
                            supplemental_paths=args.supplemental,
                            code_files=[Path(__file__).resolve(), module_path.resolve(), Path(material_reads.__file__).resolve()])
    print(json.dumps({'manifest': manifest}))


if __name__ == '__main__':
    main()
