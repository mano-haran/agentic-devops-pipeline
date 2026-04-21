import chainlit as cl

COUNTRY_CODES = [
    "AU", "BR", "CA", "DE", "ES",
    "FR", "IN", "IT", "JP", "MX",
    "NZ", "SG", "UK", "US", "ZA",
]

_FIELDS = [
    {
        "id": "bitbucket_url",
        "label": "Bitbucket Repository URL",
        "type": "text",
        "placeholder": "https://bitbucket.company.com/projects/PROJ/repos/my-service",
        "required": True,
    },
    {
        "id": "branch",
        "label": "Branch Name",
        "type": "text",
        "placeholder": "main  |  develop  |  release/2.0.0",
        "value": "main",
    },
    {
        "id": "jira_key",
        "label": "Jira Issue Key",
        "type": "text",
        "placeholder": "PROJ-1234",
        "required": True,
    },
    {
        "id": "project_type",
        "label": "Project Type",
        "type": "select",
        "options": ["MS", "MFE"],
        "value": "MS",
        "required": True,
    },
    {
        "id": "country_code",
        "label": "Country Code",
        "type": "select",
        "options": COUNTRY_CODES,
        "value": "US",
        "required": True,
    },
]


async def show_form() -> None:
    element = cl.CustomElement(
        name="PipelineForm",
        display="inline",
        props={"fields": _FIELDS},
    )
    res = await cl.AskElementMessage(
        content="Please fill in your pipeline configuration:",
        element=element,
        timeout=600,
        raise_on_timeout=False,
    ).send()

    if res and res.get("submitted"):
        await _show_summary(res)
    elif res is None:
        await cl.Message(
            content="⏰ Timed out. Type **`start`** to try again."
        ).send()
    else:
        await cl.Message(
            content="Cancelled. Type **`start`** to try again."
        ).send()


async def _show_summary(data: dict) -> None:
    rows = "\n".join(
        f"| {icon} **{label}** | `{data.get(key, '—')}` |"
        for icon, label, key in [
            ("🔗", "Bitbucket URL",  "bitbucket_url"),
            ("🌿", "Branch",         "branch"),
            ("🎫", "Jira Key",       "jira_key"),
            ("🏗️", "Project Type",   "project_type"),
            ("🌐", "Country Code",   "country_code"),
        ]
    )
    await cl.Message(
        content=(
            "## ✅  Pipeline Configuration Captured\n\n"
            "| Field | Value |\n"
            "|:------|:------|\n"
            f"{rows}\n\n"
            "Your pipeline configuration has been recorded. 🎉\n\n"
            "Type **`start`** to configure another pipeline."
        )
    ).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    await cl.Message(
        content=(
            "## 🚀  Agentic DevOps Pipeline\n\n"
            "Fill in the form below to configure your deployment pipeline."
        )
    ).send()
    await show_form()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    if message.content.lower().strip() in {"start", "restart", "new", "begin", "reset"}:
        await show_form()
    else:
        await cl.Message(
            content="Type **`start`** to open the pipeline configuration form."
        ).send()
