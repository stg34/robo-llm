from pathlib import Path
from dotenv import load_dotenv
from langfuse import get_client, observe, Evaluation
from langfuse import LangfuseMedia

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.vision_eval.frames import load_items, to_experiment_data

load_dotenv(Path(__file__).parent.parent / ".env")
langfuse = get_client()

print(langfuse.auth_check())


def load_data(dataset_name, experiment):
    # dataset_name="radiator-1"
    # dataset_name="radiator-hud-1"

    langfuse.create_dataset(name=dataset_name)

    items = load_items(experiment=experiment, path='evals/vision/frames-1.yaml')

    for item in reversed(items):
        print('---')

        text = f"Есть ли на кадре {item.object}?\nОтветь одним словом: да или нет."
        print(text)
        print(item.expected)
        print(item.frame.path)

        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={
                "question": text,
                "image": LangfuseMedia(
                    file_path=item.frame.path,
                    content_type="image/jpeg",
                ),
            },
            expected_output=item.expected,
        )

# load_data("radiator-hud-1", 2)
load_data("radiator-1", 1)
