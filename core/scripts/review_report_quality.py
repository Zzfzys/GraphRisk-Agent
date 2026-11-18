"""Prepare or assess an offline quality review. Never calls a model or tool."""
from pathlib import Path
import argparse
from pydantic import ValidationError
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.autonomous_tools import ToolBoundaryError
from graphrisk.agent.autonomous_http import strict_json
from graphrisk.agent.report_quality import prepare_quality_packet,blank_review,assess_quality,render_quality_mapping
from graphrisk.agent.upgrade_contract import canonical


def read(path):return strict_json(path.read_text(encoding='utf-8'))


def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(canonical(value)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare');prepare.add_argument('--input',type=Path,required=True);prepare.add_argument('--output',type=Path,required=True)
    assess=commands.add_parser('assess');assess.add_argument('--packet',type=Path,required=True);assess.add_argument('--review',type=Path,required=True);assess.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='prepare':
            source=read(args.input)
            bundle=EvidenceBundle.model_validate_json(canonical(source['evidence']),strict=True)
            packet=prepare_quality_packet(source['draft'],source['packet'],bundle,source_id=source['source_id'],source_kind=source['source_kind'],scenario_context=source.get('scenario_context'))
            # Prepare all artifacts before touching the new output directory.
            review=blank_review(packet);text=render_quality_mapping(packet)
            if args.output.exists():raise FileExistsError
            args.output.mkdir(parents=True)
            write_new(args.output/'quality-packet-private.json',packet)
            write_new(args.output/'review-template.json',review)
            with (args.output/'reading.md').open('x',encoding='utf-8',newline='\n') as stream:stream.write(text)
            print(canonical({'status':'prepared','review_status':'Pending','new_model_calls':0}))
        else:
            result=assess_quality(read(args.packet),read(args.review));write_new(args.output,result)
            print(canonical({'status':result['status'],'total':result['total'],'new_model_calls':0}))
    except FileExistsError:parser.exit(2,'quality_output_exists_choose_new_path\n')
    except ToolBoundaryError as error:parser.exit(2,str(error)+'\n')
    except ValidationError:parser.exit(2,'quality_review_schema_invalid_check_integer_scores_0_to_2_required_reasons_and_anchors\n')
    except (OSError,ValueError,KeyError,TypeError):parser.exit(2,'quality_input_invalid_check_frozen_source_evidence_and_file_paths_no_model_call\n')


if __name__=='__main__':main()
