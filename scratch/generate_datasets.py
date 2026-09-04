#!/usr/bin/env python3
import csv
import json
import random

# Seed for reproducibility
random.seed(42)

# Templates & vocabularies for synthetic generation
banks = ["HBL", "Easypaisa", "JazzCash", "Alfalah", "Meezan", "SCB", "UBL", "NBP"]
categories = ["groceries", "food", "entertainment", "rent", "utilities", "travel", "fuel", "shopping", "dining out"]
merchants = ["Netflix", "Spotify", "Careem", "Uber", "KFC", "McDonalds", "Daraz", "Speedy", "Savyour"]
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
amounts = ["500", "1500", "5000", "10000", "25000", "50000", "100000"]
times = ["yesterday", "today", "this morning", "last night", "last week", "two days ago"]

# ==============================================================================
# INTENT DATASET GENERATION (1000 unique examples, 200 per class)
# ==============================================================================
intent_examples = []

# Class 1: LIVE_FINANCIAL_DATA (200 examples)
live_queries = [
    "What is my current balance in {bank}?",
    "How much money do I have in my {bank} wallet?",
    "Show me my {bank} account balance as of {month}.",
    "What is my asset breakdown for my {bank} account?",
    "How much budget is left for {category} in {month}?",
    "Am I over my {category} budget this month?",
    "Show my top spending categories for {month}.",
    "Which category did I spend most of my money on in {month}?",
    "Search for {category} transactions from {time}.",
    "Did I make any payments of {amount} PKR to {merchant} {time}?",
    "Find debit transactions exceeding {amount} PKR in {month}.",
    "List all transactions under {amount} PKR for {category}.",
    "Show my credit transactions in {bank} from {time}.",
    "What was the largest expense I recorded on {category} {time}?",
    "Are there anomalies or spikes in my {category} spending?",
    "Detect any outlier transaction on my {bank} account.",
    "What is my current savings rate for my {category} goal?",
    "List my savings goals progress for {category}.",
    "Show my current ROI on investments in {bank}.",
    "How much total returns have I made on investments as of {month}?",
    "Show my recent transactions for {category} in {bank}.",
    "Retrieve transaction history from {month} for {category}.",
    "Check my budget utilization rate for {category} in {month}.",
    "Is my {bank} account balance correct for {month}?",
    "Show my latest bank statement details for {bank}.",
    "List transactions matching {merchant} under {amount} PKR.",
    "Show me my current account overview for {bank}."
]

print("Generating Class 1...")
while len(intent_examples) < 200:
    q = random.choice(live_queries).format(
        bank=random.choice(banks),
        category=random.choice(categories),
        merchant=random.choice(merchants),
        month=random.choice(months),
        amount=random.choice(amounts),
        time=random.choice(times)
    )
    item = {"question": q, "intent": "LIVE_FINANCIAL_DATA"}
    if item not in intent_examples:
        intent_examples.append(item)

# Class 2: RAG_FINANCIAL_MEMORY (200 examples)
rag_queries = [
    "How did my spending change between {month} and {month}?",
    "Compare my performance in {bank} from last month to this month.",
    "What patterns do you notice in my historical summaries for {category}?",
    "What repeating financial mistakes do I make based on my history in {month}?",
    "Give me the latest generated insight on my {category} habits.",
    "Retrieve historical insights tagged with high savings for {month}.",
    "Show my previous reports when I was in deficit during {month}.",
    "Summarize my budget trends for {category} over the last three months.",
    "Show me the comparison report for {month} and {month}.",
    "What was my biggest spending category in previous months like {month}?",
    "Did I spend more on {category} in {month} compared to {month}?",
    "Summarize my financial health in {month} based on past logs.",
    "How consistent is my savings rate historically since {month}?",
    "Give me the narrative summary of my transactions from {month}.",
    "Do you see a trend in my {category} spending?",
    "What observations did you write down in my {month} summary?",
    "Show me my long-term transaction patterns for {category}.",
    "Compare my {bank} balances between {month} and {month}.",
    "What were my core financial highlights in {month}?",
    "Analyze my historical balance history for {bank}."
]

print("Generating Class 2...")
while len(intent_examples) < 400:
    q = random.choice(rag_queries)
    
    # Check if {month} appears multiple times
    if "{month}" in q:
        m_choices = random.sample(months, 2)
        q_formatted = q.replace("{month}", m_choices[0], 1).replace("{month}", m_choices[1], 1)
    else:
        q_formatted = q

    q_formatted = q_formatted.format(
        month=random.choice(months),
        category=random.choice(categories),
        bank=random.choice(banks)
    )
    
    item = {"question": q_formatted, "intent": "RAG_FINANCIAL_MEMORY"}
    if item not in intent_examples:
        intent_examples.append(item)

# Class 3: FINANCIAL_PLANNING (200 examples)
planning_queries = [
    "Create an investment plan for my current savings of {amount} PKR.",
    "Can I afford to buy a car in {amount} months if I save in {bank}?",
    "How much should I save monthly to reach my {amount} PKR goal for {category}?",
    "Analyze my long-term runway and cash projection based on {category}.",
    "What happens to my net worth if I increase my savings by {amount} PKR?",
    "Create a 5-year roadmap for my financial independence with {bank}.",
    "When will my cash run out if I keep spending {amount} PKR on {category}?",
    "Suggest a roadmap to clear my current liabilities of {amount} PKR.",
    "How should I structure my emergency fund target of {amount} PKR?",
    "Calculate if I can retire early with my current assets in {bank}.",
    "Suggest an asset allocation plan for my {bank} savings.",
    "If I save {amount} PKR every month in {bank}, what is my net worth in 3 years?",
    "Simulate a scenario where I lose my primary income source in {month}.",
    "Can I afford a house deposit of {amount} PKR next year?",
    "What is the best way to distribute my extra savings of {amount} PKR?",
    "How much do I need to invest monthly to gain a {amount} PKR return?",
    "Suggest budget modifications for {category} to increase my savings runway."
]

print("Generating Class 3...")
while len(intent_examples) < 600:
    q = random.choice(planning_queries).format(
        amount=random.choice(amounts),
        bank=random.choice(banks),
        category=random.choice(categories),
        month=random.choice(months)
    )
    item = {"question": q, "intent": "FINANCIAL_PLANNING"}
    if item not in intent_examples:
        intent_examples.append(item)

# Class 4: HYBRID (200 examples)
hybrid_queries = [
    "Based on my current balances and goals, can I invest {amount} PKR?",
    "Will my current {category} budget allow me to double my savings goal target?",
    "Looking at my last statement from {bank} and goals, am I ready to invest {amount} PKR?",
    "With my active {bank} accounts and projected spend, when can I buy a house?",
    "Am I on track to meet my investment ROI goals based on my {category} transactions?",
    "If my {bank} balance is {amount} PKR, can I allocate 50% to my retirement goal?",
    "Check if my recent transactions for {category} match my savings goal roadmap.",
    "Based on my budget remaining, can I afford a {amount} PKR travel plan?",
    "Compare my current cash reserves in {bank} against my 6-month budget projection.",
    "Considering my recurring expenses for {category}, can I safely save {amount} PKR monthly?"
]

print("Generating Class 4...")
while len(intent_examples) < 800:
    q = random.choice(hybrid_queries).format(
        amount=random.choice(amounts),
        bank=random.choice(banks),
        category=random.choice(categories)
    )
    item = {"question": q, "intent": "HYBRID"}
    if item not in intent_examples:
        intent_examples.append(item)

# Class 5: GENERAL_FINANCIAL_KNOWLEDGE (200 examples)
general_queries = [
    "What is compound interest on a {bank} account?",
    "Explain the 50/30/20 budget rule for {category}.",
    "What is inflation and how does it affect {category} savings?",
    "What is an index fund and how is it different from a {bank} deposit?",
    "How do I create a personal budget spreadsheet for {category}?",
    "What is the difference between a mutual fund and an ETF on {bank}?",
    "Explain liquid assets vs illiquid assets in {bank}.",
    "What is a good savings rate for {category} as a young professional?",
    "What is dollar-cost averaging in a {bank} savings program?",
    "How does credit card interest accumulate on a {bank} card?",
    "What is capital gains tax on {bank} investment profits?",
    "Explain value investing vs growth investing for {category}.",
    "What is an emergency fund and how many months of {category} spending should it cover?",
    "What is a dividend yield on a {bank} stock?",
    "Explain diversification in {bank} portfolios.",
    "What is the rule of 72 for {bank} investments?",
    "How does a {bank} savings account compare to a fixed deposit?",
    "How do I allocate budget for {category} in general?",
    "What are the common risks in {category} spending?",
    "Why is {category} budget planning important?",
    "Explain the concept of compound interest for {bank} accounts.",
    "What does a financial planner do for {category} budget optimization?",
    "How does credit score affect interest rates in {bank}?",
    "What is tax tax-loss harvesting in a {bank} portfolio?",
    "Explain the difference between simple and compound interest for {bank} deposits.",
    "What is a stock market index compared to a {bank} index?",
    "What is the difference between bull and bear markets in {bank} trading?",
    "What is liquidity in finance for {category} management?",
    "What is a certificate of deposit in {bank}?",
    "Explain the concept of opportunity cost in {category} budgeting.",
    "How can I avoid credit card debt on my {bank} account?",
    "What is a high yield savings account in {bank}?",
    "What is the time value of money when budgeting for {category}?",
    "What is a treasury bill vs a {bank} bond?",
    "How do index funds track the market compared to {bank} mutual funds?",
    "What is the role of inflation in investment planning for {month}?",
    "Explain risk vs reward in {bank} portfolio construction.",
    "What is asset allocation for a {bank} portfolio?",
    "How do dividend-paying stocks work in a {bank} account?",
    "What is a bear market bounce in {month}?",
    "Explain the concept of net asset value (NAV) for {bank} funds."
]

print("Generating Class 5...")
while len(intent_examples) < 1000:
    q = random.choice(general_queries).format(
        bank=random.choice(banks),
        category=random.choice(categories),
        month=random.choice(months)
    )
    item = {"question": q, "intent": "GENERAL_FINANCIAL_KNOWLEDGE"}
    if item not in intent_examples:
        intent_examples.append(item)


# ==============================================================================
# ROUTE DATASET GENERATION (1000 unique examples)
# ==============================================================================
route_examples = []
routes_pool = [
    ("/api/agent/overview", [
        "What is my current balance in {bank}?",
        "How much money do I have in my accounts?",
        "Show my wallet balances.",
        "Give me my account balances overview.",
        "What is my current bank status?",
        "Check my total cash balance.",
        "Retrieve my current wallet totals.",
        "What are my balances in {bank} and {bank} for {month}?",
        "List all my linked bank account balances.",
        "Show current funds in my {bank} wallet."
    ]),
    ("/api/agent/analytics/net-worth", [
        "What is my net worth today?",
        "Calculate my total assets and liabilities.",
        "Show my asset breakdown.",
        "What is my balance sheet total?",
        "Calculate my current net worth in {bank} as of {month}.",
        "Show my liabilities list.",
        "What are my total debt and asset values?",
        "Check my net worth using my {bank} portfolio.",
        "Show my asset vs liability ratio.",
        "What is my overall net wealth?"
    ]),
    ("/api/agent/analytics/budget", [
        "What is the remaining balance on my {category} budget?",
        "Am I over budget this month?",
        "How much budget do I have left for {category} in my {bank} account for {month}?",
        "Check my budget progress.",
        "Show my overall spending limit progress.",
        "Did I exceed my monthly budget limit?",
        "How much room do I have left in my budget?",
        "Show my budget utilization for {category} in {month}.",
        "List all my category budgets and limits.",
        "Is my monthly {category} budget running out?"
    ]),
    ("/api/agent/analytics/top-categories", [
        "Show my top spending categories for this month.",
        "Where did most of my money go this month?",
        "List my highest spending categories.",
        "What did I spend the most money on in {bank} during {month}?",
        "Show my top 5 spending areas.",
        "Check my top expense categories.",
        "What is my top category in {month}?",
        "List category expenditure from highest to lowest.",
        "What are my main expense drivers this month?"
    ]),
    ("/api/agent/transactions/search", [
        "Search for transactions from {time}.",
        "Did I make any purchases at {merchant} recently?",
        "Find transactions over {amount} PKR.",
        "Show my recent cash deposits.",
        "List all debit transactions matching {merchant}.",
        "Search my payment logs for {merchant}.",
        "Retrieve transactions matching {category}.",
        "Find transactions matching {merchant} from {time}.",
        "Search all transactions under {amount} PKR.",
        "Find my latest transaction for {category}."
    ]),
    ("/api/agent/summary/expense-income", [
        "What was my largest transaction this week?",
        "Summarize my income vs expenses for this month.",
        "What did I spend on {category} in {bank} during {month}?",
        "Give me the monthly income and expense totals.",
        "Show my total expense vs credit ratio.",
        "Retrieve expense and income summary for {month}.",
        "Provide a summary of inflow and outflow for {month}.",
        "What is my total spending vs total earnings this month?"
    ]),
    ("/api/agent/analytics/anomalies", [
        "Are there any unusual transaction spikes on my {bank} account in {month}?",
        "Detect any anomalies in my recent spending.",
        "Show me outlier transactions.",
        "Check my accounts for suspicious activity or charges.",
        "Have I made any unusually large purchases recently?",
        "Detect any spending anomaly in {month}.",
        "Show any weird or abnormal charges on my account.",
        "Did you find any unusual transactions this week?"
    ]),
    ("/api/agent/analytics/savings", [
        "What is my current monthly savings rate?",
        "List all my active savings goals.",
        "Check my savings goals progression.",
        "How close am I to my savings goal target?",
        "Show my savings target completion percentages.",
        "How close am I to my saving goal of {amount} PKR in {bank}?",
        "Track my progress for the {category} savings goal.",
        "Am I saving enough money this month?"
    ]),
    ("/api/agent/analytics/investments", [
        "Show my investment return rate and net profit.",
        "What is my ROI on active investments?",
        "How much net return have I made on investments?",
        "Show my investment portfolio logs.",
        "Calculate my overall investment returns.",
        "Show investment profit for {month} in my {bank} portfolio.",
        "How are my investments performing?",
        "Calculate the return rate on my investments."
    ]),
    ("/api/agent/trends/monthly-balance", [
        "Show my monthly balance history for the last 3 months.",
        "What is my closing balance history.",
        "List my monthly opening and closing balances.",
        "Show balance fluctuations over time.",
        "Give me my multi-month balance trend.",
        "Show my balance trend for {bank} since {month}.",
        "List my closing balance for each of the last 6 months."
    ]),
    ("/api/agent/trends/cashflow", [
        "What is my current cashflow trend?",
        "Show my inflow vs outflow trends.",
        "Show my cashflow history logs.",
        "Analyze my monthly income and expense cashflow.",
        "Is my cashflow positive or negative?",
        "What is my cashflow for {bank} in {month}?",
        "Analyze my cashflow trend since {month}.",
        "Show daily cashflow movement for last 30 days."
    ]),
    ("/api/agent/trends/mom", [
        "Show category-wise changes for this month vs last month.",
        "What is the MoM change in my category expenses?",
        "Compare my category spending MoM.",
        "Did my category expenses increase or decrease MoM?",
        "What is the MoM change for {category}?",
        "Compare my spending in {month} vs {month} by category in {bank}."
    ]),
    ("/api/agent/analytics/statements", [
        "Summarize my performance from my latest bank statement.",
        "List my processed bank statements.",
        "Show my statement analysis results.",
        "Check statement logs for errors.",
        "Show summaries of statement metadata.",
        "Get statement report for {month} for {bank}.",
        "Show details of my uploaded bank statements."
    ]),
    ("/api/agent/analytics/projections", [
        "What is my projected month-end balance?",
        "When will my {bank} balance run out based on trend?",
        "Show my daily spending projections.",
        "Calculate my financial runway.",
        "When is my estimated zero-balance date?",
        "Show my projected ending balance for {month}.",
        "Estimate how many days of runway I have left."
    ]),
    ("/api/agent/analytics/recurring", [
        "List all my detected recurring subscriptions.",
        "Identify my recurring bills.",
        "What monthly regular bills do I pay?",
        "Show my recurring expenses list.",
        "Estimate my monthly recurring costs.",
        "List recurring items under {category} for my {bank} card.",
        "Do I have any active recurring subscriptions?"
    ])
]

# Generate exactly 1000 route examples
print("Generating Route dataset...")
while len(route_examples) < 1000:
    route, templates = random.choice(routes_pool)
    template = random.choice(templates)
    
    # Allow {bank} to be formatted twice if present
    if "{bank}" in template:
        b_choices = random.sample(banks, 2)
        q = template.replace("{bank}", b_choices[0], 1)
        q = q.replace("{bank}", b_choices[1], 1)
    else:
        q = template
        
    # Check if {month} appears multiple times
    if "{month}" in q:
        m_choices = random.sample(months, 2)
        q = q.replace("{month}", m_choices[0], 1).replace("{month}", m_choices[1], 1)

    q = q.format(
        bank=random.choice(banks),
        category=random.choice(categories),
        merchant=random.choice(merchants),
        month=random.choice(months),
        amount=random.choice(amounts),
        time=random.choice(times)
    )
        
    item = {"question": q, "api_route": route}
    if item not in route_examples:
        route_examples.append(item)


# ==============================================================================
# SAVE TO JSON & CSV
# ==============================================================================
# 1. Save Intent Dataset
intent_json_path = "/home/ezaan-amin/Projects/Aurestra/backend/scratch/intent_dataset.json"
intent_csv_path = "/home/ezaan-amin/Projects/Aurestra/backend/scratch/intent_dataset.csv"

with open(intent_json_path, "w") as f:
    json.dump(intent_examples, f, indent=2)

with open(intent_csv_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["question", "intent"])
    writer.writeheader()
    writer.writerows(intent_examples)

# 2. Save Route Dataset
route_json_path = "/home/ezaan-amin/Projects/Aurestra/backend/scratch/route_dataset.json"
route_csv_path = "/home/ezaan-amin/Projects/Aurestra/backend/scratch/route_dataset.csv"

with open(route_json_path, "w") as f:
    json.dump(route_examples, f, indent=2)

with open(route_csv_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["question", "api_route"])
    writer.writeheader()
    writer.writerows(route_examples)

print(f"Generated {len(intent_examples)} intent examples in JSON and CSV.")
print(f"Generated {len(route_examples)} route examples in JSON and CSV.")
