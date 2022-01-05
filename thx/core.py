# Copyright 2021 John Reese
# Licensed under the MIT License

import asyncio
import logging
from typing import AsyncIterator, List, Sequence

from thx.context import prepare_contexts, resolve_contexts

from .runner import prepare_job
from .types import Config, Context, Event, Job, Options, Result, Start
from .utils import timed

LOG = logging.getLogger(__name__)


def resolve_jobs(names: Sequence[str], config: Config) -> Sequence[Job]:
    queue: List[Job] = []

    for name in names:
        if name not in config.jobs:
            raise ValueError(f"unknown job {name!r}")
        job = config.jobs[name]

        if job.requires:
            deps = resolve_jobs(job.requires, config)
            for dep in deps:
                if dep not in queue:
                    queue.append(dep)

        queue.append(job)

    return queue


async def run_jobs_on_context(
    jobs: Sequence[Job], context: Context, config: Config
) -> AsyncIterator[Event]:
    for job in jobs:
        with timed("run job", context, job):
            steps = prepare_job(job, context, config)
            for step in steps:
                yield Start(command=step.cmd, job=job, context=context)
                result = await step
                yield result
                if not result.success:
                    return


async def run_jobs(
    jobs: Sequence[Job], contexts: Sequence[Context], config: Config
) -> AsyncIterator[Event]:
    if all(job.once for job in jobs):
        LOG.debug("all jobs have once=true, trimming contexts")
        contexts = contexts[0:1]
    await prepare_contexts(contexts, config)

    active_jobs: List[Job] = list(jobs)
    finished_jobs: List[Job] = []
    for context in contexts:
        with timed("run jobs", context):
            async for event in run_jobs_on_context(active_jobs, context, config):
                if isinstance(event, Start) and event.job.once:
                    finished_jobs.append(event.job)
                yield event

            # remove jobs with once=true from running on future contexts
            for job in finished_jobs:
                active_jobs.remove(job)
            finished_jobs = []


@timed("run")
def run(
    options: Options,
) -> List[Result]:
    results: List[Result] = []

    config = options.config
    contexts = resolve_contexts(config, options.python)

    job_names = options.jobs
    if not job_names:
        if config.default:
            job_names.extend(config.default)
        else:
            LOG.warning("no jobs to run")
            return []

    jobs = resolve_jobs(job_names, config)

    async def runner() -> None:
        async for event in run_jobs(jobs, contexts, config):
            print(event)

            if isinstance(event, Result):
                if not event.success:
                    print(
                        f"------------\nexit code: {event.exit_code}\n"
                        f"stdout:\n{event.stdout}\n\n"
                        f"stderr:\n{event.stderr}\n------------"
                    )
                results.append(event)

    asyncio.run(runner())
    return results
