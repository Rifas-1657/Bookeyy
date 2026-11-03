"""
app/jobs — durable DB-backed job queue (table `jobs`).

    from app.jobs import queue, worker
    job_id, created = await queue.enqueue("course_build.run", {"jobId": ...}, dedupe_key="course_build:<id>")

Workers: inline in the API process (JOBS_INLINE_WORKER=true, JOBS_CONCURRENCY) and/or
separate processes: `python -m app.worker`.  See app/jobs/queue.py for the claim/lease
semantics and app/jobs/worker.py for the handler registry.
"""
