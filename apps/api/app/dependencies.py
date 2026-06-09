from functools import lru_cache

from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.job_queue import JobQueueService
from app.services.catalog_curator import CatalogCuratorService
from app.services.run_execution import RunExecutionService
from app.services.run_repository import RunRepository
from app.services.source_lifecycle import SourceLifecycleService
from app.services.source_discovery import SourceDiscoveryService
from app.services.web_research import WebResearchService
from app.settings import Settings, get_settings


def get_app_settings() -> Settings:
    return get_settings()


@lru_cache
def get_elastic_service() -> ElasticService:
    return ElasticService(get_settings())


def get_research_agent() -> ResearchAgent:
    return ResearchAgent(get_elastic_service(), WebResearchService(get_settings()))


@lru_cache
def get_source_discovery_service() -> SourceDiscoveryService:
    return SourceDiscoveryService(get_settings())


def get_job_queue_service() -> JobQueueService:
    return JobQueueService(get_settings())


def get_catalog_curator_service() -> CatalogCuratorService:
    return CatalogCuratorService(get_settings(), get_elastic_service())


def get_run_repository() -> RunRepository:
    return RunRepository(get_elastic_service())


def get_source_lifecycle_service() -> SourceLifecycleService:
    return SourceLifecycleService(
        get_research_agent(),
        get_elastic_service(),
        get_source_discovery_service(),
        get_run_repository(),
    )


def get_run_execution_service() -> RunExecutionService:
    return RunExecutionService(
        get_research_agent(),
        get_run_repository(),
        get_source_lifecycle_service(),
    )
