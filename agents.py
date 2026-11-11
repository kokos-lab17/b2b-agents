from paypal_agent_toolkit.crewai.toolkit import PayPalToolkit
from paypal_agent_toolkit.shared.configuration import Configuration, Context
from langchain_google_genai import ChatGoogleGenerativeAI
from crewai import Agent, Task, Crew, Process
import os
import json
import re

from dotenv import load_dotenv

load_dotenv()

toolkit = PayPalToolkit(
    client_id=os.getenv("PAYPAL_CLIENT_ID"),
    secret=os.getenv("PAYPAL_CLIENT_SECRET"),
    configuration=Configuration(
        actions={"orders": {"create": True, "get": True, "capture": True}},
        context=Context(sandbox=True),
    ),
)
tools = toolkit.get_tools()
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0.4,
    api_key=os.getenv("GEMINI_API_KEY"),
)

FALLBACK_VENDORS = [
    {"name": "Vendor A", "floor": 50, "style": "aggressive", "specialty": "general"},
    {"name": "Vendor B", "floor": 55, "style": "conservative", "specialty": "premium"},
    {"name": "Vendor C", "floor": 45, "style": "volume-focused", "specialty": "bulk"},
]

sourcer = Agent(
    role="B2B Sourcing Agent",
    goal="Identify 3 credible vendors for the requested product and output structured JSON.",
    backstory=(
        "You are a B2B sourcing specialist. Given a product name and quantity, generate 3 "
        "realistic vendor profiles. Each vendor must have: name (string), floor (number, USD "
        "per unit, realistic for the product category), style (one of: aggressive, conservative, "
        "volume-focused), specialty (short string). Prices must be realistic. Output ONLY a "
        "valid JSON array. No markdown, no explanation, no code fences. Just the raw JSON array."
    ),
    tools=[],
    llm=llm,
    allow_delegation=False,
    verbose=True,
)

buyer = Agent(
    role="Procurement Agent",
    goal=(
        "Secure the best price from multiple vendors within the approved budget. "
        "Select the best value. Execute payment upon human approval."
    ),
    backstory=(
        "You are an autonomous procurement agent operating under a defined policy envelope. "
        "Your policy is set dynamically per request. You must make at least 2 counter-offers "
        "in each negotiation before accepting. You are data-driven: reference market norms, "
        "volume discounts, and competitive pressure. After all negotiations are complete, "
        "compare the final offers and select the best value (not always lowest price — consider "
        "reliability, specialty fit, and total cost). State your selection clearly: "
        "'SELECTED: <vendor_name> at $X/unit, $Y total'. Do not call pay_order."
    ),
    tools=tools,
    llm=llm,
    allow_delegation=False,
    verbose=True,
)


def create_seller_agent(vendor: dict) -> Agent:
    return Agent(
        role=f"{vendor['name']} Sales Representative",
        goal=(
            f"Close the deal above ${vendor['floor']}/unit. Create a PayPal order once terms "
            "are agreed. Never call pay_order."
        ),
        backstory=(
            f"You are the sales agent for {vendor['name']}, specializing in {vendor['specialty']}. "
            f"Your floor price is ${vendor['floor']}/unit — you will never accept below this. "
            f"Your negotiation style is {vendor['style']}. Decide your own opening price and "
            "concession strategy. Negotiation must conclude within 6 rounds. Once the buyer "
            "accepts, call create_order with intent CAPTURE for the agreed terms. Report the "
            "order_id and approve URL in your final output. You must NOT call pay_order."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )


def capture_payment(order_id: str) -> str:
    """Capture a buyer-approved PayPal order. Human approval is the only caller."""
    pay_tool = next(tool for tool in tools if tool.name == "pay_order")
    return pay_tool.run(order_id=order_id)


def _attach_step_callback(agent: Agent, step_callback) -> None:
    if step_callback is None:
        return
    role = agent.role

    def callback(step, _role=role):
        step_callback(_role, step)

    agent.step_callback = callback


def _as_float(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace("$", "").replace(",", "").strip())


def _parse_vendors(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("sourcing output did not contain a JSON array")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list) or len(data) < 3:
        raise ValueError("sourcing output did not contain 3 vendors")
    vendors = []
    for item in data[:3]:
        vendors.append(
            {
                "name": str(item["name"]).strip(),
                "floor": _as_float(item["floor"]),
                "style": str(item["style"]).strip(),
                "specialty": str(item["specialty"]).strip(),
            }
        )
    return vendors


def _result_text(result) -> str:
    parts = [str(getattr(result, "raw", result))]
    for task_output in getattr(result, "tasks_output", []) or []:
        parts.append(str(getattr(task_output, "raw", task_output)))
    return "\n".join(parts)


def run_procurement(
    product: str,
    qty: int,
    budget: float,
    step_callback=None,
    on_vendors=None,
) -> dict:
    """Full pipeline: source → negotiate x3 → compare → order."""
    _attach_step_callback(sourcer, step_callback)
    sourcing_crew = Crew(
        agents=[sourcer],
        tasks=[
            Task(
                description=(
                    f"Find 3 vendors for: {product}, quantity {qty}. "
                    "Output ONLY a JSON array."
                ),
                expected_output=(
                    '[{"name":"...","floor":...,"style":"...","specialty":"..."}]'
                ),
                agent=sourcer,
            )
        ],
        process=Process.sequential,
        verbose=True,
    )
    sourcing_result = sourcing_crew.kickoff()
    try:
        vendors = _parse_vendors(str(getattr(sourcing_result, "raw", sourcing_result)))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        vendors = [dict(vendor) for vendor in FALLBACK_VENDORS]

    if on_vendors is not None:
        on_vendors(vendors)

    sellers = [create_seller_agent(vendor) for vendor in vendors]
    for seller in sellers:
        _attach_step_callback(seller, step_callback)

    neg_tasks = []
    for vendor in vendors:
        neg_tasks.append(
            Task(
                description=(
                    f"Negotiate with {vendor['name']} for {qty} units of {product}. "
                    f"Vendor profile: floor ${vendor['floor']}/unit, style {vendor['style']}, "
                    f"specialty {vendor['specialty']}. Your budget cap is ${budget}/unit. "
                    "Make at least 2 counter-offers. Conclude within 6 rounds. "
                    f"Final message must state: "
                    f"'AGREED with {vendor['name']}: $X/unit, $Y total'."
                ),
                expected_output=f"Final agreed price with {vendor['name']}",
                agent=buyer,
            )
        )

    compare_task = Task(
        description=(
            "Based on all three negotiation results above, compare the final offers. "
            "Select the best value considering price, vendor reliability, and specialty fit. "
            "State: 'SELECTED: <vendor_name> at $X/unit, $Y total'."
        ),
        expected_output="Selected vendor name, unit price, and total",
        agent=buyer,
        context=neg_tasks,
    )

    order_agent = Agent(
        role="Order Execution Agent",
        goal="Create a PayPal order for the selected vendor and agreed price.",
        backstory=(
            "You create PayPal orders. You receive the selected vendor name, agreed unit "
            "price, and quantity. Call create_order with intent CAPTURE. Report order_id and "
            "approve URL. Do NOT call pay_order."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )
    _attach_step_callback(buyer, step_callback)
    _attach_step_callback(order_agent, step_callback)

    order_task = Task(
        description=(
            f"Using the selected vendor and agreed price from the comparison, call create_order "
            f"with intent CAPTURE for {qty} units of {product} at the agreed unit price. "
            f"Pass currency_code 'USD' and one item named '{product}' with quantity {qty}, "
            f"item_cost equal to the agreed unit price, item_total equal to item_cost times "
            f"{qty}, and tax_percent 0. The toolkit sends intent CAPTURE automatically. "
            "Report the order_id and the approve URL (the link whose rel is approve). "
            "Do NOT call pay_order."
        ),
        expected_output="Order ID and approve URL",
        agent=order_agent,
        context=[compare_task],
    )

    crew = Crew(
        agents=[buyer] + sellers + [order_agent],
        tasks=neg_tasks + [compare_task, order_task],
        process=Process.sequential,
        verbose=True,
        planning=True,
        planning_llm=llm,
    )
    result = crew.kickoff()
    return {
        "raw": getattr(result, "raw", str(result)),
        "vendors": vendors,
        "transcript": _result_text(result),
    }
