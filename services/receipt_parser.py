import json
import requests

def parse_receipt_text(text: str) -> dict:
    extracted = {
        "amount": None,
        "sender": None,
        "recipient": None,
        "transaction_date": None,
        "transaction_time": None,
        "transaction_id": None,
        "payment_provider": None,
    }

    print("====== IMAGE TEXT (OCR) ======")
    print(repr(text))
    print("==============================")

    if not text:
        print("====== NO TEXT FOUND IN IMAGE ======")
        return extracted

    prompt = f"""You are an expert financial receipt parser. Extract the details into a pure JSON object.
Use exactly these keys: "amount" (number), "sender" (string), "recipient" (string), "transaction_date" (string), "transaction_time" (string), "transaction_id" (string), "payment_provider" (string).

Rules for extraction:
1. Sender: Look for names after "Sent by", "From", or similar. If not found, default to "EZAAN AMIN".
2. Recipient: Look carefully for names after "Bank Account", "Paid to", "Sent to", "Transfer to", "To", or at the top of the receipt.
3. If the receipt says "You have received money" and no clear recipient is found, set recipient to "EZAAN AMIN".
4. For all other fields, if the value is not found, set it to null.
5. Provide ONLY valid JSON. Do not include markdown formatting or anything outside the JSON object.

Receipt text:
{text}
"""

    print("====== IMAGE TEXT (OCR) ======")
    print(text)
    print("==============================")
    
    print("====== LLM PROMPT ======")
    print(prompt)
    print("========================")

    try:
       
        response = requests.post('http://nascar-biography-greater-musical.trycloudflare.com/api/generate', json={
            "model": "qwen2.5:3b",
            "prompt": prompt,
            "format": "json",
            "stream": False
        }, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            llm_response = data.get('response', '')
            
            print("====== LLM RAW OUTPUT ======")
            print(llm_response)
            print("============================")
            
            # Clean up potential markdown formatting
            cleaned_response = llm_response.strip()
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response[7:]
            elif cleaned_response.startswith('```'):
                cleaned_response = cleaned_response[3:]
            if cleaned_response.endswith('```'):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()
            
            parsed = json.loads(cleaned_response)
            
            # Ensure all keys exist
            extracted = {
                "amount": parsed.get("amount"),
                "sender": parsed.get("sender"),
                "recipient": parsed.get("recipient"),
                "transaction_date": parsed.get("transaction_date"),
                "transaction_time": parsed.get("transaction_time"),
                "transaction_id": parsed.get("transaction_id"),
                "payment_provider": parsed.get("payment_provider"),
            }
            
            # Additional cleanup for amount
            if extracted["amount"] is not None:
                try:
                    extracted["amount"] = float(str(extracted["amount"]).replace(',', ''))
                except ValueError:
                    pass
            
            return extracted
        else:
            print("====== LLM ERROR RESPONSE ======")
            print(f"Status: {response.status_code}")
            print(f"Text: {response.text}")
            print("================================")
            
    except Exception as e:
        print("====== LLM EXCEPTION ======")
        print("LLM Extraction failed:", e)
        print("===========================")
        
    return extracted
