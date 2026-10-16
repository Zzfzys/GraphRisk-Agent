"""Materialize a pinned, deterministic Stage09 deployment profile from route4."""
from pathlib import Path
import yaml
from graphrisk.graph.deployment import materialize
def main():
    root=Path(__file__).resolve().parents[1]
    path=root/"artifacts/stage-09/route5/20261007-replay-v1/deployment.json"
    profile=materialize(root,path)
    config=yaml.safe_load((root/"configs/stage05.yaml").read_text(encoding="utf-8"))
    config.update(model_policy=path.relative_to(root).as_posix(),model_policy_sha256=profile["content_sha256"])
    config["paths"]["run_dir"]="reports/generated/stage09/service"
    dest=root/"configs/stage09.yaml"
    text=yaml.safe_dump(config,allow_unicode=True,sort_keys=False)
    if dest.exists() and dest.read_text(encoding="utf-8")!=text:
        raise ValueError("config exists with a different model profile; preserve and review")
    dest.write_text(text,encoding="utf-8")
    print({"model_policy_sha256":profile["content_sha256"],"config":str(dest),"score_mode":"xgboost_only"})
if __name__=="__main__":main()

