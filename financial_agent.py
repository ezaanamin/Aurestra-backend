import argparse
import datetime
import json
from calendar import monthrange
from database import app, db
from model import Transaction, Budget, FinancialInsight
from sqlalchemy import extract, func

class FinancialAgent:
    def __init__(self):
        pass

    def analyze_month(self, year, month):
        """
        Main entry point to analyze a specific month.
        """
        print(f"🤖 Financial Agent: Analyzing {year}-{month:02d}...")
        
        with app.app_context():
            # 1. Fetch Data
            transactions = self.fetch_transactions(year, month)
            budget = self.fetch_budget(year, month)
            
            # 2. Calculate Metrics
            metrics = self.calculate_metrics(transactions, budget, year, month)
            
            # 3. Generate Narrative
            narrative = self.generate_narrative(metrics, year, month)
            
            # 4. Decide & Persist
            if self.decide_storage(metrics):
                self.persist_memory(year, month, narrative, metrics)
            else:
                print("ℹ️ Analysis complete. No high-signal insights to store.")

    def fetch_transactions(self, year, month):
        return Transaction.query.filter(
            extract('year', Transaction.date) == year,
            extract('month', Transaction.date) == month
        ).all()

    def fetch_budget(self, year, month):
        month_str = f"{year}-{month:02d}"
        return Budget.query.filter_by(month=month_str).first()

    def calculate_metrics(self, transactions, budget, year, month):
        total_income = 0.0
        total_expense = 0.0
        categories = {}
        
        # Determine Income from Budget (Projected) or Credits (Actual)
        # Using Actual Credits for "True" Income in this context, or Budget if available?
        # Let's use Actual Credits for strict analysis
        
        input_income = 0.0
        
        for tx in transactions:
            category = tx.purpose or "Uncategorized"
            if tx.type == 'credit':
                input_income += tx.amount
            elif tx.type == 'debit':
                total_expense += tx.amount
                categories[category] = categories.get(category, 0) + tx.amount
        
        # If Budget exists, use that as the "Expected Income" baseline?
        # Let's stick to actuals for "History", but maybe note budget adherence.
        # For simplicity, we use Actual Income - Actual Expense
        
        # Validating with Budget if no income found (common in manual entry apps)
        if input_income == 0 and budget:
            input_income = budget.total_budget

        net_savings = input_income - total_expense
        savings_rate = (net_savings / input_income * 100) if input_income > 0 else 0.0

        # Sort categories
        sorted_cats = sorted(categories.items(), key=lambda item: item[1], reverse=True)
        top_categories = sorted_cats[:3]

        return {
            "income": input_income,
            "expense": total_expense,
            "net_savings": net_savings,
            "savings_rate": savings_rate,
            "top_categories": top_categories, # List of (Name, Amount)
            "transaction_count": len(transactions),
            "category_breakdown": categories
        }

    def generate_narrative(self, metrics, year, month):
        """
        Generates a natural language summary (Third Person).
        Optimized for RAG: Returns distinct, factual sentences.
        """
        month_name = datetime.date(year, month, 1).strftime("%B %Y")
        
        income = metrics['income']
        expense = metrics['expense']
        savings = metrics['net_savings']
        rate = metrics['savings_rate']
        top_cats = metrics['top_categories']
        
        sentences = []
        
        # Sentence 1: High-level summary
        sentences.append(f"In {month_name}, Ezaan recorded a total income of {income:,.0f} and total expenses of {expense:,.0f}.")
        
        # Sentence 2: Savings Performance
        if savings >= 0:
            sentences.append(f"This resulted in a net savings of {savings:,.0f}, achieving a savings rate of {rate:.1f}%.")
        else:
            sentences.append(f"This resulted in a deficit of {abs(savings):,.0f}, meaning expenses exceeded income.")

        # Sentence 3: Top Spend Categories
        if top_cats:
            cat_text = ", ".join([f"{c[0]} ({c[1]:,.0f})" for c in top_cats])
            sentences.append(f"The top spending categories for Ezaan were: {cat_text}.")
            
        # Join with double newlines for clear separation in UI and RAG chunking
        return "\n\n".join(sentences)

    def decide_storage(self, metrics):
        """
        Decides if this month is worth storing. 
        Currently: ALWAYS store monthly summaries as they are the baseline for future comparisons.
        """
        # We could filter out empty months
        if metrics['income'] == 0 and metrics['expense'] == 0:
            return False
        return True

    def persist_memory(self, year, month, content, metrics):
        month_str = f"{year}-{month:02d}"
        
        tags = []
        if metrics['savings_rate'] > 20:
            tags.append("high_savings")
        if metrics['net_savings'] < 0:
            tags.append("deficit")
        
        # Check if exists
        existing = FinancialInsight.query.filter_by(month=month_str).first()
        if existing:
            print(f"⚠️ Insight for {month_str} already exists. Updating...")
            existing.content = content
            existing.metrics_json = json.dumps(metrics)
            existing.tags = ",".join(tags)
        else:
            new_insight = FinancialInsight(
                month=month_str,
                content=content,
                metrics_json=json.dumps(metrics),
                tags=",".join(tags)
            )
            db.session.add(new_insight)
            
        db.session.commit()
        print(f"✅ Saved Financial Insight for {month_str}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=str, help="YYYY-MM to analyze. Defaults to last month.", default=None)
    args = parser.parse_args()

    agent = FinancialAgent()
    
    if args.month:
        dt = datetime.datetime.strptime(args.month, "%Y-%m")
        agent.analyze_month(dt.year, dt.month)
    else:
        # Default to previous month
        today = datetime.date.today()
        first = today.replace(day=1)
        last_month = first - datetime.timedelta(days=1)
        agent.analyze_month(last_month.year, last_month.month)
