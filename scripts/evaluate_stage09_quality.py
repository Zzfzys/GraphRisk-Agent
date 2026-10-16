"""Route5A local-only, frozen quality comparison."""
import argparse
from pathlib import Path
from graphrisk.demo.stage09_quality import prepare,develop,final_run,check

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','develop','run','check'])
    p.add_argument('--run-dir',default='reports/generated/stage09/route5a/20261008-quality-v2');args=p.parse_args()
    root=Path(__file__).resolve().parents[1];directory=(root/args.run_dir).resolve()
    if not directory.is_relative_to(root/'reports/generated/stage09/route5a') or directory==root/'reports/generated/stage09/route5a':
        p.error('run-dir must be a dedicated route5a directory')
    {'prepare':prepare,'develop':develop,'run':final_run,'check':check}[args.action](root,directory)
if __name__=='__main__':main()
