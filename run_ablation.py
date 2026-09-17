import subprocess

print("Running ablation...")
subprocess.run(["uv", "run", "python", "eval/ablation.py"])
