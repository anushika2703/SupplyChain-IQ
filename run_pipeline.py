from src.pipeline import run_pipeline
if __name__ == "__main__":
    for k, v in run_pipeline().items():
        print(f"{k}: {v}")
