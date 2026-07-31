import asyncio
from app.services.nli_verifier import NLIVerifierService

async def run_test():
    verifier = NLIVerifierService()
    source_text = 'The organization created a new category for funding.'
    generated_claim = 'The organization bought a cat.'
    
    print(f"Source Text: '{source_text}'")
    print(f"Generated Claim: '{generated_claim}'")

    # 1. Run the verification (no 'await' needed)
    result = verifier.verify_claim(generated_claim, [source_text])
    
    # 2. Safely dump the Pydantic model to a dictionary
    try:
        result_dict = result.model_dump()
    except AttributeError:
        result_dict = vars(result)
        
    print(f"\nVerification Result: {result_dict}")
    
    # 3. FIX: Determine support by checking the Enum 'label' attribute, not 'is_supported'
    label_str = str(result.label).lower()
    is_supported = "not_entailed" not in label_str
    
    print(f"Supported? {is_supported}")
    print(f"Score: {result.score}")

if __name__ == "__main__":
    asyncio.run(run_test())