import os
import json
import shutil
from tqdm import tqdm
from pydantic import ValidationError
from datasets import load_dataset
from judges.cpp_judge import CppJudge
from judges.python_judge import PythonJudge
from judges.java_judge import JavaJudge
from utils.logger import Logger, JSONLogger
from utils.models import Problem, Config
from utils.utils import sanitize_filename
from providers.openai import OpenAIProvider
from providers.huggingface import HuggingFaceProvider
from providers.anthropic import AnthropicProvider
from providers.mistral import MistralProvider
from providers.google import GoogleProvider

def load_problems_from_hf(dataset_name: str, split: str = 'train') -> list[str]:
    dataset = load_dataset(dataset_name, split=split)
    return [json.dumps(problem) for problem in dataset]

def load_config(config_path: str) -> Config:
    with open(config_path, 'r') as file:
        config_json = json.load(file)
    return Config(**config_json)

def generate_summary(results: list[dict]) -> str:
    passed_count = sum(1 for result in results if result['pass'])
    total_count = len(results)
    return f"Passed {passed_count}/{total_count} test cases"

def load_existing_log(log_filename: str) -> dict:
    if os.path.exists(log_filename):
        with open(log_filename, 'r') as file:
            return json.load(file)
    return {}

def initialize_provider(config: Config, logger: Logger):
    if config.provider == "openai":
        return OpenAIProvider(config.api_key, config.model, config.base_prompt, logger, config.language)
    elif config.provider == "huggingface":
        return HuggingFaceProvider(config.model, config.base_prompt, logger, config.language)
    elif config.provider == "anthropic":
        return AnthropicProvider(config.api_key, config.model, config.base_prompt, logger, config.language)
    elif config.provider == "mistral":
        return MistralProvider(config.api_key, config.model, config.base_prompt, logger, config.language)
    elif config.provider == "google":
        return GoogleProvider(config.api_key, config.model, config.base_prompt, logger, config.language)
    else:
        logger.log('error', "Invalid provider specified")
        raise ValueError("Invalid provider specified")

def initialize_judge(language: str, logger: Logger):
    if language == "cpp":
        return CppJudge(logger)
    elif language == "python":
        return PythonJudge(logger)
    elif language == "java":
        return JavaJudge(logger)
    else:
        logger.log('error', "Unsupported language specified")
        raise ValueError("Unsupported language specified")

def process_problem(judge, provider, problem_data: dict, shots: int, ignore_time_limits: bool, json_logger: JSONLogger, logger: Logger, problems_passed: int, total_filtered_problems: int, index: int) -> int:
    problem_title = problem_data['title']
    sanitized_title = sanitize_filename(problem_title)

    for shot in range(1, shots + 1):
        solution = provider.generate_solution(problem_data)
        if solution:
            if isinstance(judge, JavaJudge):
                try:
                    class_name = judge.get_class_name(solution)
                    source_file = os.path.join("temp", f"{class_name}.java")
                    binary_file = os.path.join("temp", f"{class_name}.class")
                except ValueError as e:
                    logger.log('error', str(e))
                    json_logger.log_compilation_error(problem_data["title"], problem_data.get("category", "Uncategorized"), solution, str(e), problems_passed, shot)
                    continue
            else:
                source_file = os.path.join("temp", f"{sanitized_title}_shot_{shot}.{judge.language_extension}")
                binary_file = os.path.join("temp", f"{sanitized_title}_binary_shot_{shot}")

            with open(source_file, 'w') as file:
                file.write(solution)

            if isinstance(judge, PythonJudge):
                compile_success = True
            else:
                compile_success = judge.compile_code(source_file, binary_file)

            if compile_success:
                try:
                    problem = Problem(**problem_data)
                    results = []
                    for test_case in problem.test_cases:
                        input_data = test_case.input
                        if isinstance(judge, PythonJudge):
                            result = judge.run_code(source_file, input_data, problem.time_limit, problem.memory_limit, ignore_time_limits)
                        elif isinstance(judge, JavaJudge):
                            result = judge.run_code(class_name, input_data, problem.time_limit, problem.memory_limit, ignore_time_limits)
                        else:
                            result = judge.run_code(binary_file, input_data, problem.time_limit, problem.memory_limit, ignore_time_limits)
                        
                        result['pass'] = judge.validate_output(result['output'], test_case.output)
                        result['log'] = result.get('error', '') or ('Passed' if result['pass'] else 'Failed')
                        results.append(result)

                    summary = generate_summary(results)
                    logger.log('info', f"Problem {index + 1}/{total_filtered_problems} Shot {shot}: {summary}")
                    if all(result['pass'] for result in results):
                        problems_passed += 1
                        json_logger.log_problem(problem.title, problem.category or "Uncategorized", results, solution, problems_passed, {"shot": shot, "status": "passed"})
                        break
                    else:
                        json_logger.log_problem(problem.title, problem.category or "Uncategorized", results, solution, problems_passed, {"shot": shot, "status": "failed"})
                except ValidationError as e:
                    logger.log('error', f"Problem validation error: {e}")
            else:
                logger.log('error', "Compilation failed")
                json_logger.log_compilation_error(problem_data["title"], problem_data.get("category", "Uncategorized"), solution, "Compilation failed", problems_passed, shot)
        else:
            logger.log('error', "Solution generation failed")
            json_logger.log_compilation_error(problem_data["title"], problem_data.get("category", "Uncategorized"), "No solution generated", "Solution generation failed", problems_passed, shot)

    return problems_passed

def main():
    logger = Logger()
    config = load_config('config.json')

    os.makedirs("benchmark", exist_ok=True)
    os.makedirs("temp", exist_ok=True)

    log_filename = os.path.join("benchmark", f"{sanitize_filename(config.provider)}_{sanitize_filename(config.model)}_{sanitize_filename(config.language)}_log.json")

    if not config.continue_from_log:
        if os.path.exists(log_filename):
            os.remove(log_filename)
        json_logger = JSONLogger(log_filename)
        json_logger.log_initial_config(config)
    else:
        json_logger = JSONLogger(log_filename)

    problems = load_problems_from_hf("juvi21/cses-fi-competitive-coding-problems")

    categories_filter = config.categories
    shots = config.shots
    ignore_time_limits = config.ignore_time_limits

    judge = initialize_judge(config.language, logger)
    provider = initialize_provider(config, logger)

    if categories_filter:
        filtered_problems = [problem for problem in problems if json.loads(problem).get("category") in categories_filter]
    else:
        filtered_problems = problems

    total_filtered_problems = len(filtered_problems)
    problems_passed = json_logger.data.get("total_passed_problems", 0)
    processed_titles = set(problem["title"] for problem in json_logger.data.get("problems", []))

    for index, problem_str in enumerate(tqdm(filtered_problems, desc="Processing problems")):
        problem_data = json.loads(problem_str)
        problem_title = problem_data['title']

        if problem_title in processed_titles:
            logger.log('info', f"Skipping already processed problem: {problem_title}")
            continue

        logger.log('info', f"Judging problem: {problem_title}")
        problems_passed = process_problem(judge, provider, problem_data, shots, ignore_time_limits, json_logger, logger, problems_passed, total_filtered_problems, index)

    if os.path.exists("temp"):
        shutil.rmtree("temp")

if __name__ == "__main__":
    main()
