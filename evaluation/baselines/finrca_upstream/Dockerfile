# Isolated runner for the upstream FinRCA Rules/SQL and classical-ML baselines.
# Versions match the dependency versions recorded in the upstream run manifests
# (results/*/run_manifest.json at revision 76c93ec). openai/pydantic/tiktoken are
# not imported by these baselines and are not installed.
FROM python:3.11.3-slim
# The ML runner shells out to `git rev-parse HEAD` and crashes if git is absent;
# a stub that fails makes it record UNAVAILABLE_NOT_A_GIT_WORKTREE, as it does for non-git trees.
RUN printf '#!/bin/sh\nexit 128\n' > /usr/local/bin/git && chmod +x /usr/local/bin/git
RUN pip install --no-cache-dir numpy==1.26.0 pandas==2.1.1 scikit-learn==1.4.1.post1 \
    joblib==1.3.2 PyYAML==6.0.1 pytest==8.4.2
COPY audit_run.py run_baselines.sh /opt/
WORKDIR /work
ENTRYPOINT ["bash", "/opt/run_baselines.sh"]
