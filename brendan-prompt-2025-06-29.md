# System Instructions

You will be provided with the following data for each unique prescription (name + rx number):

Do your best:

JSON Data for processing: { json_data } Patient Task History Details: { patient_task_history_details } Bil Notes: { bil_notes } Gen Notes: { gen_notes } Bil Notes Summary Instructions: { bil_notes_summary_instructions } Gen Notes Summary Instructions: { gen_notes_summary_instructions }

Execute the following Python code with json_data as input:
from itertools import tee, filterfalse

def strcmp_nocase(s1, s2):
"""case-insensitive string comparison"""
return s1.casefold() == s2.casefold()

def partition(pred, iterable):
t1, t2 = tee(iterable)
return filterfalse(pred, t1), filter(pred, t2)

def is_base_mention(note_record):
return strcmp_nocase(note_record["base_name"], note_record["prescription"])

def add_base_notes_if_match(base, det):
if strcmp_nocase(det["base_name"], base["base_name"]):
det["notes"].extend(base["notes"])
return det

def add_bases(bases, details):
return ([add_base_notes_if_match(b, det)
for b in bases
for det in details])

def process_prescriptions(json_data):
note_records = json.loads(json_data)
details, bases = partition(is_base_mention, note_records)
return add_bases(bases, details)
Here is an example of what the python code should be doing.

Q: [

{

"noteTxt": "MD CALLED FOR BIKTARVY 50 MG-200 MG-25 MG TABLET",

"noteSummaryTxt": "MD CALLED FOR BIKTARVY 50/200/25 TAB",

"createDate": "04/18/2024 09:15:30 AM",

"noteId": "2147951001",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "PATIENT QUESTION RE RX#10219991",

"noteSummaryTxt": "PATIENT QUESTION RE DUPIXENT PEN",

"createDate": "04/18/2024 11:22:05 AM",

"noteId": "2147951002",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "VERIFIED DOSING FOR EPOGEN 10,000 UNIT/ML INJECTION SOLUTION",

"noteSummaryTxt": "VERIFIED DOSING EPOGEN 10K U/ML SOLN",

"createDate": "04/19/2024 02:45:11 PM",

"noteId": "2147951003",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "QUESTION ABOUT A5 EPOGEN 10,000 UNIT/ML INJECTION SOLUTION",

"noteSummaryTxt": "VERIFIED DOSING A5 EPOGEN 10K U/ML SOLN",

"createDate": "04/19/2024 02:45:11 PM",

"noteId": "2147951003",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "PT CALLED REGARDING A5 EPOGEN",

"noteSummaryTxt": "CALL RE A5 EPOGEN",

"createDate": "04/19/2024 02:45:11 PM",

"noteId": "2147951003",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "ADDED NOTES FOR BIKTARVY",

"noteSummaryTxt": "ADDED NOTES FOR BIKTARVY",

"createDate": "04/19/2024 04:10:00 PM",

"noteId": "2147951004",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
},

{

"noteTxt": "PA REQUIRED FOR DUPIXENT",

"noteSummaryTxt": "PA REQUIRED FOR DUPIXENT",

"createDate": "04/20/2024 08:55:39 AM",

"noteId": "2147951005",

"noteTypeCd": "BIL",

"noteTypeInd": "BILLING/PRICING/PRIOR AUTH"
},

{

"noteTxt": "RX RECEIVED FOR epoetin alfa (PROCRIT) injection 40,000 units",

"noteSummaryTxt": "RX RCVD PROCRIT INJ 40K UNITS",

"createDate": "04/20/2024 10:01:52 AM",

"noteId": "2147951006",

"noteTypeCd": "GEN",

"noteTypeInd": "GENERAL"
}

{

"finalDisplayEntityId": "10219990-00",

"medicineName": "biktarvy 50 mg-200 mg-25 mg tablet"
},

{

"finalDisplayEntityId": "10219991-00",

"medicineName": "dupixent 300 mg/2 ml subcutaneous pen injector"
},

{

"finalDisplayEntityId": "10219992-00",

"medicineName": "epogen 10,000 unit/ml injection solution"
},

{

"finalDisplayEntityId": "10219993-00",

"medicineName": "epoetin alfa injection 40,000 units"
}

]

A:

{

"biktarvy": [

"2147951004"
],

"biktarvy 50 mg-200 mg-25 mg tablet": [

"2147951001",

"2147951004"
],

"dupixent": [

"2147951005"
],

"dupixent 300 mg/2 ml subcutaneous pen injector": [

"2147951002",

"2147951005"
],

"epogen": [

"2147951003"
],

"epogen 10,000 unit/ml injection solution": [

"2147951003"
],

"epoetin alfa injection 40,000 units": [

"2147951006"
]

}

```json {
"properties": {
"taskName": {
"description": "From **Patient Task History Details** ,Specify the **Bpm Task Name** including its **Task status**. Pick **Bpm Task name** and append with its corresponding **Task status** (e.g., 'PA Tasks - Completed').",
"title": "Taskname",
"type": "string",
"pattern": "(?i)^(PA Task|PA Tasks) - (Assigned|Unassigned|Completed|Cancelled)$"
},
"rxNumber": {
"description": "Specify the prescription number (Rx) related to this task. Fetch the Rx Number from the field finalDisplayEntityId which is present in the table Patient Task History Details . If the Rx number is not explicitly mentioned in the source, leave this field empty. Rx number is critical for us, make sure to include it if it is available.",
"title": "Rxnumber",
"type": "string"
},
"drugName": {
"description": "Provide the name of the drug associated with the task. Example: [Enbrel 20mg],[Humira Pen]. Make sure the drug name corresponds to the Rx number provided.",
"title": "Drugname",
"type": "string"
},
"bilNotesFacts":{
"description": "please write the main topic, action and actors of the bil Notes here",
"items": {
"type": "array"
},
"title": "bilNotesFacts",
"type": "array"
},
"bilNotesSummary": {
"description": "Follow bil_notes_summary_instructions to provide the summary.",
"items": {
"type": "string"
},
"title": "Bilnotessummary",
"type": "string"
},
"genNotesFacts":{
"description": "please write the main topic, action and actors of the bil Notes here",
"items": {
"type": "array"
},
"title": "genNotesFacts",
"type": "array"
},
"genNotesSummary": {
"description": "Follow gen_notes_summary_instructions to provide the summary.",
"items": {
"type": "string"
},
"title": "Gennotessummary",
"type": "string"
}
},
"required": [
"taskName",
"rxNumber",
"drugName",
"bilNotesFacts",
"bilNotesSummary",
"genNotesFacts",
"genNotesSummary"
],
"title": "individualTasksPa",
"type": "object"
}```

Use the following data mapping guidelines:

taskName: Extract from patient_task_history_details.
rxNumber: Extract from patient_task_history_details.
drugName: Extract from the output of the Python code.
bilNotesFacts: Extract from bil_notes.
bilNotesSummary: Generate based on bil_notes and bil_notes_summary_instructions.
genNotesFacts: Extract from gen_notes.
genNotesSummary: Generate based on gen_notes and gen_notes_summary_instructions.
Return the final JSON output with a unique key:value for each drug + rx number combination. Nota Bene: Only include bilNotesSummary and genNotesFacts for the drug mentioned that unique drug + rx number combination.
Example: So for example, the bilNotesSummary for Vitriol 2/Box Rx XXXXXXXX-XX should only include mentions of any drug starting with Vitriol 2/bBox XXXXXXXX-XXand any drugs that are not mapped to a prescription number.

Here is the DATA: DATA: [{&quot;noteTxt&quot;: &quot;PATIENT WANTS TO TALK TO MD REGARDING RX#10210309&quot;, &quot;noteSummaryTxt&quot;: &quot;PATIENT WANTS TO TALK TO MD REGARDING RX#10210309&quot;, &quot;createDate&quot;: &quot;03/25/2025 07:31:49 PM&quot;, &quot;noteId&quot;: &quot;2147949351&quot;, &quot;noteTypeCd&quot;: &quot;BIL&quot;, &quot;noteTypeInd&quot;: &quot;BILLING/PRICING/PRIOR AUTH&quot;}, {&quot;noteTxt&quot;: &quot;NPS REPORTED FOR FEP PRESCRIPTIONS FOR HUMIRA&quot;, &quot;noteSummaryTxt&quot;: &quot;NPS REPORTED FOR FEP PRESCRIPTIONS FOR HUMIRA&quot;, &quot;createDate&quot;: &quot;03/21/2025 03:51:41 PM&quot;, &quot;noteId&quot;: &quot;2147945703&quot;, &quot;noteTypeCd&quot;: &quot;BIL&quot;, &quot;noteTypeInd&quot;: &quot;BILLING/PRICING/PRIOR AUTH&quot;}, {&quot;noteTxt&quot;: &quot;TOPIC : INSURANCE CELL PHONE NUMBER : 4379883916 EMAIL ADDRESS : Anup.Kumar2@CVSHealth.com TEXT SENT ON:03/21/2025 01:39:46 PM AKUMAR4(EMP) Sent : SMS TO PATIENT : Test EMAIL TO PATIENT : test EMAIL SUBJECT LINE : test SECURE MESSAGE BEHIND LINK : Insurance team want to inform the update in the covergae for drug HUMIRA PEN (2/BOX) (ABB) BrandIND:CVS OrgId:1 DivisionId:1 ClientCD:CVS LOBCode:00 CarrierId: AccountNo: GroupNo:&quot;, &quot;noteSummaryTxt&quot;: &quot;<SM><INSURANCE(SM)>&quot;, &quot;createDate&quot;: &quot;03/21/2025 03:39:46 PM&quot;, &quot;noteId&quot;: &quot;2147945694&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;MD CALLED FOR HUMIRA PEN (2/BOX) (ABB)&quot;, &quot;noteSummaryTxt&quot;: &quot;MD CALLED FOR HUMIRA PEN (2/BOX) (ABB)&quot;, &quot;createDate&quot;: &quot;03/21/2025 01:36:33 PM&quot;, &quot;noteId&quot;: &quot;2147945570&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;MD CALLED FOR HUMIRA&quot;, &quot;noteSummaryTxt&quot;: &quot;MD CALLED FOR HUMIRA&quot;, &quot;createDate&quot;: &quot;03/20/2025 05:34:53 PM&quot;, &quot;noteId&quot;: &quot;2147944956&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;ADDED NOTES FOR HUMIRA&quot;, &quot;noteSummaryTxt&quot;: &quot;ADDED NOTES FOR HUMIRA&quot;, &quot;createDate&quot;: &quot;03/20/2025 10:48:33 AM&quot;, &quot;noteId&quot;: &quot;2147944391&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;GEN NOTE ADDED FOR T6 HUMIRA&quot;, &quot;noteSummaryTxt&quot;: &quot;GEN NOTE ADDED FOR T6 HUMIRA&quot;, &quot;createDate&quot;: &quot;03/20/2025 12:21:52 AM&quot;, &quot;noteId&quot;: &quot;2147944061&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;GEN NOTE ADDED

FOR HUMIRA PEN(2/BOX) (ABB)&quot;, &quot;noteSummaryTxt&quot;: &quot;GEN NOTE ADDED FOR HUMIRA PEN(2/BOX) (ABB)&quot;, &quot;createDate&quot;: &quot;03/20/2025 12:21:11 AM&quot;, &quot;noteId&quot;: &quot;2147944059&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;GEN NOTE ADDED FOR HUMIRA 40 MG SYRINGE(2/BOX)&quot;, &quot;noteSummaryTxt&quot;: &quot;GEN NOTE ADDED FOR HUMIRA 40 MG SYRINGE(2/BOX)&quot;, &quot;createDate&quot;: &quot;03/20/2025 12:20:29 AM&quot;, &quot;noteId&quot;: &quot;2147944058&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;Received <8797086> with <RX> for <HUMIRA 20MG SYRINGE> within SPRx Intake. Doc(s) can be viewed within Pt SPRx Profile-Images&quot;, &quot;noteSummaryTxt&quot;: &quot;Received <8797086> with <RX> for <HUMIRA 20MG SYR&quot;, &quot;createDate&quot;: &quot;03/19/2025 07:06:30 PM&quot;, &quot;noteId&quot;: &quot;2147943900&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;noteTxt&quot;: &quot;Patient enrolled via < CAREGIVER PHONE ENROLLMENT > by < QATST_R1 >, Phone/Fax #: < >, < > for < HUMIRA 20MG SYRINGE, TRUVADA >. Method to obtain rx: < >. Referring MD: < > < >.&quot;, &quot;noteSummaryTxt&quot;: &quot;New Patient Enrollment&quot;, &quot;createDate&quot;: &quot;03/19/2025 07:01:09 PM&quot;, &quot;noteId&quot;: &quot;2147943894&quot;, &quot;noteTypeCd&quot;: &quot;GEN&quot;, &quot;noteTypeInd&quot;: &quot;GENERAL&quot;}, {&quot;finalDisplayEntityId&quot;: &quot;10210308-00&quot;, &quot;medicineName&quot;: &quot;HUMIRA 40MG SYRINGE (2/BOX)&quot;}, {&quot;finalDisplayEntityId&quot;: &quot;10210310-00&quot;, &quot;medicineName&quot;: &quot;HUMIRA PEN (2/BOX) (ABB)&quot;}, {&quot;finalDisplayEntityId&quot;: &quot;10210311-00&quot;, &quot;medicineName&quot;: &quot;T6 HUMIRA&quot;}, {&quot;finalDisplayEntityId&quot;: &quot;10210309-00&quot;, &quot;medicineName&quot;: &quot;HUMIRA PEN (2/BOX)&quot;}
