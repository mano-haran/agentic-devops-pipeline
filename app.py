import chainlit as cl

COUNTRY_CODES = [
    "AU", "BR", "CA", "DE", "ES",
    "FR", "IN", "IT", "JP", "MX",
    "NZ", "SG", "UK", "US", "ZA",
]

_DEFAULTS = {
    "bitbucket_url": "",
    "branch": "main",
    "jira_key": "",
    "project_type": "MS",
    "country_code": "US",
}


def _widgets():
    return [
        cl.input_widget.TextInput(
            id="bitbucket_url",
            label="Bitbucket Repository URL",
            placeholder="https://bitbucket.company.com/projects/PROJ/repos/my-service",
        ),
        cl.input_widget.TextInput(
            id="branch",
            label="Branch Name",
            initial="main",
            placeholder="main  |  develop  |  release/2.0.0",
        ),
        cl.input_widget.TextInput(
            id="jira_key",
            label="Jira Issue Key",
            placeholder="PROJ-1234",
        ),
        cl.input_widget.Select(
            id="project_type",
            label="Project Type  (MS = Microservice  |  MFE = Micro Frontend)",
            values=["MS", "MFE"],
            initial_index=0,
        ),
        cl.input_widget.Select(
            id="country_code",
            label="Country Code",
            values=COUNTRY_CODES,
            initial_index=COUNTRY_CODES.index("US"),
        ),
    ]


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    cl.user_session.set("config", settings)


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("config", _DEFAULTS.copy())
    await _show_form()


async def _show_form() -> None:
    await cl.ChatSettings(_widgets()).send()

    await cl.Message(
        content=(
            "## 🚀  Agentic DevOps Pipeline\n\n"
            "A **Pipeline Configuration Form** is now available.\n\n"
            "1. Click the **⚙️ settings icon** (top-right of this window)\n"
            "2. Fill in all five fields\n"
            "3. Come back here and click **Submit Configuration** ↓"
        )
    ).send()

    await _prompt_submit()


async def _prompt_submit() -> None:
    res = await cl.AskActionMessage(
        content="When you've filled the form, click Submit:",
        actions=[
            cl.Action(name="submit", value="submit", label="✅  Submit Configuration"),
            cl.Action(name="reset",  value="reset",  label="🔄  Reset & Start Over"),
        ],
        timeout=600,
        raise_on_timeout=False,
    ).send()

    if not res:
        await cl.Message(content="⏰ Timed out. Type **`start`** to try again.").send()
        return

    if res["value"] == "reset":
        cl.user_session.set("config", _DEFAULTS.copy())
        await _show_form()
        return

    config = cl.user_session.get("config") or _DEFAULTS.copy()

    missing = [
        label for label, key in [
            ("Bitbucket URL", "bitbucket_url"),
            ("Jira Issue Key", "jira_key"),
        ]
        if not config.get(key, "").strip()
    ]
    if missing:
        await cl.Message(
            content=f"⚠️  Please fill in: **{', '.join(missing)}** — then click Submit again."
        ).send()
        await _prompt_submit()
        return

    await _show_summary(config)


async def _show_summary(config: dict) -> None:
    rows = "\n".join(
        f"| {icon} **{label}** | `{config.get(key, '—')}` |"
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


@cl.on_message
async def on_message(message: cl.Message) -> None:
    if message.content.lower().strip() in {"start", "restart", "new", "begin", "reset"}:
        await _show_form()
    else:
        await cl.Message(
            content=(
                "I'm the **DevOps Pipeline Trigger** assistant.\n\n"
                "Type **`start`** to open the pipeline configuration form."
            )
        ).send()
