# RLM Inner-Loop State Diagram

This artifact visualizes the exact state transitions inside the custom DSPy Recursive Language Model (`RLMEngine`). It maps the recursive behavior that ensures the engine effectively executes generated code against the stateful Modal Workspace.

## The DSPy RLMEngine Execution Loop

```mermaid
stateDiagram-v2
    direction TB

    [*] --> Idle : Delegated by Supervisor

    state Idle {
        [*] --> Initialize_Context
    }
    Idle --> Task_Decomposition : Supervisor provides Task Goal

    state Task_Decomposition {
        %% Generates plan based on task and filesystem context
        [*] --> Generate_MultiStep_Plan
    }
    Task_Decomposition --> Code_Generation : Feed Plan Step N

    state Code_Generation {
        %% Uses CodeWriterSignature
        [*] --> Draft_Python_Code
    }
    Code_Generation --> Modal_Execution : Execute `execute_workspace_code`

    state Modal_Execution {
        %% The execution sandbox running in a Modal container
        [*] --> Run_Python
        Run_Python --> Capture_Stdout
        Capture_Stdout --> Persist_Filesystem_State
    }

    Modal_Execution --> Context_Evaluation : Return stdout & workspace context

    state Context_Evaluation {
        %% RLM interprets the physical environment to verify logic
        [*] --> Evaluate_Success
        Evaluate_Success --> Check_Truncation_Guard : Output Length > 2000?
        Check_Truncation_Guard --> Check_Syntax_Error : Verify traceback
    }

    %% Conditional Routing based on Evaluation
    Context_Evaluation --> Code_Generation : Syntax/Execution Error (Retry with trace)
    Context_Evaluation --> Truncation_Guard_Triggered : Length > 2000 context limit

    state Truncation_Guard_Triggered {
        [*] --> Append_Warning
        note right of Append_Warning
            "WARNING: Output truncated. Context window
            protected. Write scripts to filter
            or save to files instead."
        end note
    }
    Truncation_Guard_Triggered --> Code_Generation : Forces RLM to rewrite code

    Context_Evaluation --> Next_Plan_Step : Execution Successful
    Next_Plan_Step --> Code_Generation : If Plan is incomplete (Step N+1)
    Next_Plan_Step --> Complete : All Plan steps succeeded

    state Complete {
        [*] --> Return_Final_Observation
    }
    Complete --> [*] : Supervisor receives final answer
```
