import time
from pathlib import Path
from dotenv import load_dotenv
from langfuse import get_client, observe, Evaluation
from langfuse import LangfuseMedia

load_dotenv(Path(__file__).parent.parent / ".env")
langfuse = get_client()

# print(langfuse.auth_check())

# with langfuse.start_as_current_observation(name="TEST-GEN", as_type="span") as gen:
#     time.sleep(1)
#     gen.update(input="IN", output="OUT")
#
#
#
#     with langfuse.start_as_current_observation(name="INNER-GEN", as_type="generation",  model="claude-opus-5", usage_details={"input": 100, "output": 50}) as inner_gen:
#         time.sleep(1)
#         img = LangfuseMedia(file_path="docs/vision-tools-analysis/frame-02.jpg", content_type="image/jpeg")
#         inner_gen.update(input=img, output="INNER-OUT")
#         # print(langfuse.get_trace_url())
#
#     print(langfuse.get_trace_url())
#
# langfuse.flush()

# ----------------------------------------------------------------------------------------------------------------------

data = [
    {
        "input": "1",
        "expected_output": 1
    },
    {
        "input": "2",
        "expected_output": 2
    },
    {
        "input": "201",
        "expected_output": 200
    }
]

def my_task(*, item, **kwargs):
    return int(item["input"]) + 0.5

def evaluator(*, input, output, expected_output, metadata, **kwargs):
      return Evaluation(name="str-to-int-error", value=expected_output - output)

# def evaluator(value, ground_truth):
#     return Evaluation(name="str-to-int-error", value=ground_truth - value)

result = langfuse.run_experiment(
    name="str to int",
    run_name="test-02",
    data=data,                  # список item'ов
    task=my_task,               # функция: item -> ответ модели
    evaluators=[evaluator],     # оценки по каждому item
    max_concurrency=2
)

print(result.format())
langfuse.flush()
