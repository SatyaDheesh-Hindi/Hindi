import os
import sys

from hindi_core import Translator

def main():
    print("--- STARTING TEST WITH SARVAM-1 ---")
    t = Translator()
    
    test_english_article = """
    Delhi Police have arrested three men for allegedly cheating people through a fake job website. 
    Officials said the gang collected over Rs 40 lakh from at least 200 applicants. 
    The website has been taken down and an investigation is on. 
    The startup founder said that AI tools like ChatGPT will help junior developers code faster, 
    but they still need human oversight to prevent critical bugs in the system.
    """
    
    print("\n--- ORIGINAL ENGLISH TEXT ---")
    print(test_english_article.strip())
    print("-" * 50)
    
    print("\nTranslating to Conversational Hindi using Sarvam-1...")
    
    hindi_translation = t.en2hi(test_english_article)
    
    print("\n--- TRANSLATED CONVERSATIONAL HINDI ---")
    print(hindi_translation)
    print("-" * 50)
    print("Test completed.")

if __name__ == "__main__":
    main()
