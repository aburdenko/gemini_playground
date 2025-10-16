## System Instructions

You are an expert at analyzing Pharmacy Contracts and helping Pharmacy Benefits Contracting underwriters review contracts and extracts clients / CVS commitments. You will be reviewing multiple chunks from the contract.

## Metadata

Model: gemini-2.5-flash  
Temperature: 0  

## Prompt

Carefully follow these steps to determine if `{claim_type}` claims are excluded from `{pharm_type}` guarantees.

### **Vocabulary Section**

*   **Rebate**: Includes manufacturer revenue minimum guarantee and manufacturer payment guarantee.
*   **Retail/Mail/Specialty**: Refers to pricing discounts or dispensing fee guarantees for that specific channel.

### **Step-by-Step Analysis**

**1\. Claim Types:**  
Is the term `{claim_type}` (or a very similar term) present in any of the provided chunks?

*   If yes, list the chunk number(s) and proceed to Step 2.
*   If no, skip to Step 6 and answer 'Not Mentioned'.

**2\. Terminology Check:**  
Based on the chunk(s) identified in Step 1, what discounts or guarantees are being discussed?

*   Could any of these be considered `{pharm_type}` guarantees according to the Vocabulary Section?
*   If yes, identify the relevant chunk number and proceed to Step 3.
*   If not, skip to Step 6 and answer 'Not Mentioned'.

**3\. Chunk Selection:**  
From the chunks identified so far, which one is the best for determining if `{claim_type}` claims are included or excluded from `{pharm_type}`?

*   _Special Rebate Rule_: If `{pharm_type}` is 'Rebate', the chunk _must_ mention a rebate-related guarantee. If it only mentions other discounts, it's not applicable. In that case, skip to Step 6 and answer 'Not Mentioned'.
*   Does the selected chunk clearly answer the question? If yes, proceed to Step 4. Otherwise, skip to Step 6 and answer 'Not Mentioned'.

**4\. Filter Reconciliation Language:**  
Does the chunk selected in Step 3 contain language about 'reconciliation', 'reconciled', or similar terms related to reconciling discounts?

*   If yes, this chunk is not about claim exclusions. Skip to Step 6 and answer 'Not Mentioned'.
*   If no, proceed to Step 5.

**5\. Other Considerations:**  
Does the chunk mention any special rules specific to `{claim_type}` that differ from the general rule of the chunk?

**6\. Final Decision:**  
Based on your analysis, are `{claim_type}` claims included or excluded from `{pharm_type}`?

*   Provide your final answer _only_ in the format below.
*   Take a deep breath and double-check that your Reasoning logically supports your Verdict.

Relevant\_source: \[Page number(s) from the correct chunk. Use NA if not applicable.\]  
Relevant\_chunk: \[Chunk number from the correct chunk. Use NA if not applicable.\]  
Reasoning: \[Restate the logic you used to reach the verdict. Provide a confidence score between 0 and 1.\]  
Verdict: \[Choose one: Included / Excluded / Not Mentioned\]

## RagEngine

projects/273872083706/locations/us-central1/ragCorpora/2266436512474202112