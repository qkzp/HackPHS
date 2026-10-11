"""Desktop launcher and reproducible headless verification commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import logging
import numpy as np

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    __package__='AeroSlice'


def make_examples(folder: Path) -> None:
    from .model import box_mesh,write_stl
    from .analysis import synthetic_readings,save_csv
    folder.mkdir(parents=True,exist_ok=True)
    if not (folder/'demo_payload.stl').exists():
        write_stl(folder/'demo_payload.stl',box_mesh(np.array([-15.,-15.,0.]),np.array([15.,15.,30.])))
    if not (folder/'synthetic_sensor.csv').exists(): save_csv(synthetic_readings(),folder/'synthetic_sensor.csv')


def main() -> None:
    parser=argparse.ArgumentParser(description='AeroSlice desktop / authentic slicer verification')
    parser.add_argument('--demo',action='store_true',help='Generate examples and run a real slicer optimization (no fabricated estimates).')
    parser.add_argument('--check-compatibility',action='store_true',help='Execute real cube + modifier slicing and save compatibility evidence.')
    parser.add_argument('--output',type=Path,default=Path('outputs'))
    parser.add_argument('--orca',default='')
    parser.add_argument('--profiles',default='')
    parser.add_argument('--semiautomated',action='store_true')
    parser.add_argument('--model',type=Path)
    parser.add_argument('--fea-config',type=Path,help='JSON FEASettings inputs for experimental simulation-guided optimization.')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s: %(message)s')
    examples=Path(__file__).resolve().parent/'examples';make_examples(examples)
    if args.demo or args.check_compatibility or args.model or args.fea_config:
        from .file_import import load_model
        from .optimizer import optimize
        from .structural import weight_saving_settings
        from .slicer import OrcaSlicer,Profiles
        fea=None
        if args.fea_config:
            from .fea import FEASettings
            fea=FEASettings(**json.loads(args.fea_config.read_text()));fea.validate()
        slicer=OrcaSlicer(args.orca,args.profiles);profiles=Profiles();baseline=15.
        try: baseline=slicer.starting_infill(profiles.process)
        except (OSError,ValueError,KeyError): pass
        report=optimize(load_model(args.model or examples/'demo_payload.stl'),weight_saving_settings(baseline),args.output,
                        slicer,profiles,print,args.semiautomated,fea)
        print(report['status']);print(report['message']);print(report['run_directory'])
        if args.check_compatibility:
            shutil.copy2(Path(report['run_directory'])/'compatibility_report.json',args.output/'compatibility_report.json')
        if report['status']=='FAILED': raise SystemExit(1)
    else:
        from .gui import Application
        Application(examples=examples).mainloop()

if __name__=='__main__': main()
