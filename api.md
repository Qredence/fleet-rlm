# Health

Types:

```python
from fleet_rlm_typescript_sdk.types import HealthCheckResponse
```

Methods:

- <code title="get /health">client.health.<a href="./src/fleet_rlm_typescript_sdk/resources/health.py">check</a>() -> <a href="./src/fleet_rlm_typescript_sdk/types/health_check_response.py">HealthCheckResponse</a></code>

# Ready

Types:

```python
from fleet_rlm_typescript_sdk.types import ReadyCheckResponse
```

Methods:

- <code title="get /ready">client.ready.<a href="./src/fleet_rlm_typescript_sdk/resources/ready.py">check</a>() -> <a href="./src/fleet_rlm_typescript_sdk/types/ready_check_response.py">ReadyCheckResponse</a></code>

# Chat

Methods:

- <code title="post /chat">client.chat.<a href="./src/fleet_rlm_typescript_sdk/resources/chat.py">send_message</a>(\*\*<a href="src/fleet_rlm_typescript_sdk/types/chat_send_message_params.py">params</a>) -> object</code>

# Tasks

Types:

```python
from fleet_rlm_typescript_sdk.types import TaskRequest, TaskResponse
```

Methods:

- <code title="post /tasks/check-secret">client.tasks.<a href="./src/fleet_rlm_typescript_sdk/resources/tasks.py">check_secret</a>() -> <a href="./src/fleet_rlm_typescript_sdk/types/task_response.py">TaskResponse</a></code>
- <code title="post /tasks/architecture">client.tasks.<a href="./src/fleet_rlm_typescript_sdk/resources/tasks.py">run_architecture</a>(\*\*<a href="src/fleet_rlm_typescript_sdk/types/task_run_architecture_params.py">params</a>) -> <a href="./src/fleet_rlm_typescript_sdk/types/task_response.py">TaskResponse</a></code>
- <code title="post /tasks/basic">client.tasks.<a href="./src/fleet_rlm_typescript_sdk/resources/tasks.py">run_basic</a>(\*\*<a href="src/fleet_rlm_typescript_sdk/types/task_run_basic_params.py">params</a>) -> <a href="./src/fleet_rlm_typescript_sdk/types/task_response.py">TaskResponse</a></code>
- <code title="post /tasks/long-context">client.tasks.<a href="./src/fleet_rlm_typescript_sdk/resources/tasks.py">run_long_context</a>(\*\*<a href="src/fleet_rlm_typescript_sdk/types/task_run_long_context_params.py">params</a>) -> <a href="./src/fleet_rlm_typescript_sdk/types/task_response.py">TaskResponse</a></code>
