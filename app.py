import chainlit as cl

COUNTRY_CODES = [
    "AU", "BR", "CA", "DE", "ES",
    "FR", "IN", "IT", "JP", "MX",
    "NZ", "SG", "UK", "US", "ZA",
]


async def ask_text_field(step: str, title: str, icon: str, hint: str, example: str) -> str | None:
    """Show a labelled element then capture a text answer via AskElementMessage."""
    label_el = cl.Text(
        name=f"label_{step}",
        content=(
            f"{icon}  {title}\n"
            f"{'─' * 40}\n"
            f"{hint}\n"
            f"Example: {example}"
        ),
        display="inline",
    )
    res = await cl.AskElementMessage(
        content=f"**Step {step}**  — {title}",
        elements=[label_el],
        timeout=300,
        raise_on_timeout=False,
    ).send()
    return res["output"].strip() if res else None


async def ask_action_field(step: str, title: str, choices: list[tuple[str, str]]) -> str | None:
    """Show labelled action buttons and return the clicked value."""
    actions = [
        cl.Action(name=f"choice_{step}", value=val, label=label)
        for val, label in choices
    ]
    res = await cl.AskActionMessage(
        content=f"**Step {step}**  — {title}",
        actions=actions,
        timeout=300,
        raise_on_timeout=False,
    ).send()
    return res["value"] if res else None


async def run_form() -> None:
    config: dict[str, str] = {}

    # ── Step 1: Bitbucket URL ──────────────────────────────────────────────
    val = await ask_text_field(
        "1 / 5", "Bitbucket Repository URL", "🔗",
        "Full URL of the Bitbucket repository to deploy.",
        "https://bitbucket.company.com/projects/PROJ/repos/my-service",
    )
    if val is None:
        await _timeout_msg()
        return
    config["bitbucket_url"] = val

    # ── Step 2: Branch ────────────────────────────────────────────────────
    val = await ask_text_field(
        "2 / 5", "Branch Name", "🌿",
        "Git branch that will be built and deployed.",
        "main  |  develop  |  release/2.1.0",
    )
    if val is None:
        await _timeout_msg()
        return
    config["branch"] = val

    # ── Step 3: Jira Key ──────────────────────────────────────────────────
    val = await ask_text_field(
        "3 / 5", "Jira Issue Key", "🎫",
        "Jira ticket that tracks this deployment.",
        "PROJ-1234  |  DEPLOY-567",
    )
    if val is None:
        await _timeout_msg()
        return
    config["jira_key"] = val.upper()

    # ── Step 4: Project Type (MS / MFE) ───────────────────────────────────
    val = await ask_action_field(
        "4 / 5", "🏗️  Project Type — choose one:",
        [
            ("MS",  "🔷  MS  — Microservice"),
            ("MFE", "🔶  MFE — Micro Frontend"),
        ],
    )
    if val is None:
        await _timeout_msg()
        return
    config["project_type"] = val

    # ── Step 5: Country Code ──────────────────────────────────────────────
    val = await ask_action_field(
        "5 / 5", "🌐  Country Code — select target region:",
        [(c, f"🌍  {c}") for c in COUNTRY_CODES],
    )
    if val is None:
        await _timeout_msg()
        return
    config["country_code"] = val

    await show_summary(config)


async def show_summary(config: dict[str, str]) -> None:
    """Print a markdown table of all captured values."""
    rows = "\n".join(
        f"| {icon} **{label}** | `{config[key]}` |"
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
            "Here is a summary of what you entered:\n\n"
            "| Field | Value |\n"
            "|:------|:------|\n"
            f"{rows}\n\n"
            "Your configuration has been recorded. 🎉\n\n"
            "Type **`start`** to configure another pipeline."
        )
    ).send()


async def _timeout_msg() -> None:
    await cl.Message(
        content="⏰ No response received. Type **`start`** to try again."
    ).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    await cl.Message(
        content=(
            "## 🚀  Agentic DevOps Pipeline\n\n"
            "Welcome! I'll walk you through **5 quick steps** to capture your "
            "pipeline configuration.\n\n"
            "Let's begin ↓"
        )
    ).send()
    await run_form()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    cmd = message.content.lower().strip()
    if cmd in {"start", "restart", "new", "begin", "reset"}:
        await run_form()
    else:
        await cl.Message(
            content=(
                "I'm the **DevOps Pipeline Trigger** assistant.\n\n"
                "Type **`start`** to fill in a new pipeline configuration."
            )
        ).send()
