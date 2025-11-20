# linkedin/campaigns.py
import logging
from datetime import timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Literal, Union

import yaml
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    ConfigDict,
)

logger = logging.getLogger(__name__)


# ==============================================================
# Shared config
# ==============================================================

class DelaysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_min: int = Field(..., ge=0)
    delay_max: int = Field(..., ge=0)

    @field_validator("delay_max")
    @classmethod
    def check_max(cls, v: int, info):
        if "delay_min" in info.data and v < info.data["delay_min"]:
            raise ValueError("delay_max must be >= delay_min")
        return v


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_connections: Optional[int] = Field(None, ge=0)
    daily_messages: Optional[int] = Field(None, ge=0)


class SettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: Optional[LimitsConfig] = None
    delays: Optional[DelaysConfig] = None


# ==============================================================
# Step-specific parameter models
# ==============================================================

class ScrapeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_csv: str
    output_csv: Optional[str] = None


class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_file: Optional[str] = None
    template_type: Optional[Literal["static", "jinja", "ai_prompt"]] = None
    # Add more action-specific fields here in the future


class ConditionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout: str  # e.g. "14d"
    check_interval: Optional[str] = None  # e.g. "6h"


# ==============================================================
# Raw step models (one per type)
# ==============================================================

class BaseRawStep(BaseModel):
    handler: str = Field(..., description="Dotted path to function")


class ScrapeStep(BaseRawStep):
    step_type: Literal["scrape"]
    scrape: ScrapeParams


class ActionStep(BaseRawStep):
    step_type: Literal["action"]
    action: ActionParams


class ConditionStep(BaseRawStep):
    step_type: Literal["condition"]
    condition: ConditionParams


RawStep = Union[ScrapeStep, ActionStep, ConditionStep]


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_name: str
    steps: List[RawStep] = Field(..., min_length=1)
    settings: Optional[SettingsConfig] = None


# ==============================================================
# Runtime step classes
# ==============================================================

def import_callable(path: str) -> Callable:
    from importlib import import_module
    mod_name, func_name = path.rsplit(".", 1)
    return getattr(import_module(mod_name), func_name)


def parse_duration(s: str) -> timedelta:
    s = s.strip().lower()
    num = int("".join(filter(str.isdigit, s)))
    if s.endswith("d"): return timedelta(days=num)
    if s.endswith("h"): return timedelta(hours=num)
    if s.endswith("m"): return timedelta(minutes=num)
    if s.endswith("s"): return timedelta(seconds=num)
    raise ValueError(f"Invalid duration: {s}")


class ExecutableStep:
    def __init__(self, raw: RawStep, num: int, campaign: str):
        self.num = num
        self.campaign = campaign
        self.raw = raw
        self.handler = import_callable(raw.handler)

        if isinstance(raw, ScrapeStep):
            self.type = "scrape"
            self.params = raw.scrape.model_dump()
        elif isinstance(raw, ActionStep):
            self.type = "action"
            self.params = raw.action.model_dump()
        else:  # ConditionStep
            self.type = "condition"
            cond = raw.condition
            self.timeout = parse_duration(cond.timeout)
            self.check_interval = parse_duration(cond.check_interval) if cond.check_interval else None
            self.params = {}  # conditions usually don't need extra params

    def execute(self, context: Dict[str, Any], profile: Optional[Dict[str, Any]] = None):
        context["params"] = self.params
        logger.info(f"[{self.campaign}] Step {self.num} [{self.type.upper()}] → {self.raw.handler}")
        if self.type == "scrape":
            return self.handler(context)  # no profile
        else:
            return self.handler(context, profile)  # action or condition

    def __repr__(self):
        return f"<Step #{self.num} {self.type} → {self.raw.handler}>"


# ==============================================================
# Campaign
# ==============================================================

class ParsedCampaign:
    def __init__(self, config: CampaignConfig, path: Path):
        self.name = config.campaign_name
        self.path = path
        self.settings = config.settings or SettingsConfig()
        self.steps = [ExecutableStep(raw, i + 1, self.name) for i, raw in enumerate(config.steps)]

    def __repr__(self):
        return f"<Campaign '{self.name}' [{len(self.steps)} steps]>"


# ==============================================================
# Loader
# ==============================================================

def load_campaigns(dir_path: Optional[str] = None) -> List[ParsedCampaign]:
    if dir_path is None:
        dir_path = Path.cwd() / "assets" / "campaigns"
    path = Path(dir_path)
    if not path.is_dir():
        raise ValueError(f"Campaign dir not found: {path}")

    campaigns = []
    for file in path.glob("*.y*ml"):
        data = yaml.safe_load(file.read_text())
        config = CampaignConfig.model_validate(data)
        campaigns.append(ParsedCampaign(config, file))
        logger.info(f"Loaded {config.campaign_name}")
    return campaigns


# ==============================================================
# CLI
# ==============================================================

if __name__ == "__main__":
    # Forcefully reset and configure logging to ensure reliability
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear any existing handlers to avoid conflicts
    logging.basicConfig(
        level=logging.DEBUG,  # Set to DEBUG for more logs; change to INFO if too verbose
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    try:
        for c in load_campaigns():
            print(c)
            for s in c.steps:
                print("  ", s)
    except Exception as e:
        print("Error:", e)
