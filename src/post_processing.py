import os
import importlib.util
import copy
from typing import List, Dict

from post_processing_base import PostProcessor


class PostProcessingManager:
    def __init__(self):
        self.scripts_folder = "postprocess"
        self.processors: List[PostProcessor] = []
        self._load_processors()

    def _load_processors(self):
        if not os.path.exists(self.scripts_folder):
            return

        script_files = sorted(
            [
                f
                for f in os.listdir(self.scripts_folder)
                if f.endswith(".py") and not f.startswith("_")
            ]
        )

        for script_name in script_files:
            script_path = os.path.join(self.scripts_folder, script_name)
            try:
                spec = importlib.util.spec_from_file_location(script_name, script_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                processor_class = getattr(module, "Processor")
                if issubclass(processor_class, PostProcessor):
                    self.processors.append(processor_class())
                else:
                    print(
                        f"Warning: {script_name} does not contain a valid Processor class"
                    )
            except Exception as e:
                print(f"Error loading {script_name}: {str(e)}")

    def process(self, transcription: Dict) -> Dict:
        result = copy.deepcopy(transcription)
        result["processed"] = copy.copy(result["raw_text"])
        for processor in self.processors:
            result = processor.process(result)
        return result
