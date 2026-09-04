# scratch/test_model_loading.py
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

def test():
    intent_model_dir = "/home/ezaan-amin/Projects/Aurestra/backend/ai_models/financial_intent_classifier"
    route_model_dir = "/home/ezaan-amin/Projects/Aurestra/backend/ai_models/financial_api_route_classifier"
    
    print("Loading intent classifier...")
    intent_tok = AutoTokenizer.from_pretrained(intent_model_dir)
    intent_mod = AutoModelForSequenceClassification.from_pretrained(intent_model_dir)
    intent_mod.eval()
    
    print("Loading route classifier...")
    route_tok = AutoTokenizer.from_pretrained(route_model_dir)
    route_mod = AutoModelForSequenceClassification.from_pretrained(route_model_dir)
    route_mod.eval()
    
    test_texts = [
        "What is my current balance in Easypaisa?",
        "Explain the 50/30/20 budget rule for groceries.",
        "How did my spending change between January and February?",
        "Did I make any payment to Careem yesterday?",
        "Show my top spending categories for May"
    ]
    
    for text in test_texts:
        # Intent classification
        inputs = intent_tok(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            outputs = intent_mod(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1)
        pred_idx = torch.argmax(probs, dim=-1).item()
        intent = intent_mod.config.id2label.get(pred_idx) or intent_mod.config.id2label.get(str(pred_idx))
        intent_conf = probs[0][pred_idx].item()
        
        route = None
        route_conf = 0.0
        if intent == "LIVE_FINANCIAL_DATA":
            # Route classification
            inputs_route = route_tok(text, return_tensors="pt", truncation=True, max_length=512)
            with torch.no_grad():
                outputs_route = route_mod(**inputs_route)
            probs_route = torch.softmax(outputs_route.logits, dim=-1)
            pred_idx_route = torch.argmax(probs_route, dim=-1).item()
            route = route_mod.config.id2label.get(pred_idx_route) or route_mod.config.id2label.get(str(pred_idx_route))
            route_conf = probs_route[0][pred_idx_route].item()
            
        print(f"Text: '{text}'")
        print(f"  -> Intent: {intent} (conf: {intent_conf:.4f})")
        if route:
            print(f"  -> Route: {route} (conf: {route_conf:.4f})")
        print("-" * 50)

if __name__ == "__main__":
    test()
