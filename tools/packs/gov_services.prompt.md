# Pack brief: gov_services (Aldervale Services Desk)

## The business

The Province of Aldervale is a fictional province with two official languages, English and
French. Its services desk (Bureau des services d'Aldervale) answers questions about three
programmes, proof of residence and its privacy notice. Offices: ALV-CTR Downtown Service
Centre / Centre de services du centre-ville, ALV-NORD North Service Office / Bureau de services
du Nord, ALV-PORT Harbour Service Office / Bureau de services du Port, ALV-MOB Mobile Service
Unit / Unité mobile de services. Applications are made on the online portal, at an office or by
mail. The core files (system prompt, skills, tools, facts, episodes) are given to you; the
documents must agree with them exactly.

## The feature

Bilingual grounded answers: every rule the assistant gives comes from a cited document, in the
language of the question, and personal numbers or addresses typed into chat are redacted by
the input guardrails. The English and French document of a programme state exactly the same
rules and numbers; the French ones are written in natural Canadian French (not a word-for-word
translation), with amounts written as 450 $ and dates as le 15 décembre 2026.

## Rules the documents must state (use these numbers exactly)

Home Heating Rebate (HHR) / remise pour le chauffage domestique:
- Eligible: an Aldervale resident for at least 12 months before applying; the home is the
  principal residence; household income for 2025 under 58,000 dollars; the household pays for
  heating (if heating is included in the rent, a letter from the landlord replaces the bill).
- One rebate per household per heating season.
- Amount: 450 dollars, or 600 dollars when a member of the household is 65 or older.
- Documents: one proof of residence, the most recent heating bill (dated within the last
  3 months) or the landlord letter, and the 2025 provincial notice of assessment.
- Apply from 15 September 2026 to 15 December 2026 for the 2026-2027 season; late
  applications are not accepted.
- Decision within 25 business days of a complete application; payment by direct deposit
  within 10 business days of approval.
- A refusal can be appealed within 30 days of the decision letter.

Child Activity Credit (CAC) / crédit pour les activités des enfants:
- For a child under 16 on 31 December 2026 (under 18 for a child with a disability).
- The activity: a registered sport, arts or learning activity lasting at least 8 weeks, or a
  day camp of at least 5 consecutive days.
- Credit: 50 percent of the fees, up to 300 dollars per child per year (500 dollars for a child
  with a disability).
- Documents: receipts showing the organisation, the child's name, the fees paid and the dates;
  up to 3 receipts per claim, one claim per child per year.
- Claims for activities in 2026 are accepted until 31 December 2026.

Senior Transit Pass (STP) / laissez-passer de transport pour aînés:
- For residents aged 65 or older.
- Free unlimited travel on provincial buses and the harbour ferry; 50 percent off regional rail.
- Documents: photo ID showing the date of birth, and one proof of residence. The pass photo is
  taken at the Downtown or Harbour office, or by the mobile unit.
- Valid 2 years; renew up to 60 days before it expires.
- Processing takes 15 business days; the card is mailed. A lost card costs 10 dollars to
  replace.

Proof of residence / preuve de résidence (one document, showing the applicant's name and the
same address as the application):
- Accepted: an Aldervale driver's licence with the current address; a lease or a property tax
  notice; a utility bill (electricity, water, heating) dated within the last 3 months; a bank
  statement dated within the last 3 months; a letter from a shelter or a care home.
- Not accepted: mobile phone bills, post office box addresses, handwritten notes.

Privacy notice / avis de confidentialité:
- The desk collects reference numbers and the programme information needed to answer.
- Chat transcripts are kept 90 days, then deleted. Social insurance numbers, dates of birth
  and street addresses typed into chat are replaced by placeholders before anything is stored.
- The desk never asks for a social insurance number in chat.
- People may ask to see or correct their information through the privacy officer (the online
  portal's contact form). Information is never sold or shared outside the province's services.

The two guides (heating_rebate_guide in English, guide_remise_chauffage in French) are the
long, complete versions of the Home Heating Rebate rules: overview, who is eligible, documents,
three ways to apply (portal, office, mail), what happens after (decision, payment), appeals,
and the five most common mistakes (an old heating bill, a mobile phone bill as proof of
residence, a missing notice of assessment, applying twice for one household, applying after
15 December). Each guide is 750 to 900 words.

## Documents

Write the documents the docs plan lists. Do not write any phone number, email address, web
address or street address, and no office street addresses: name offices only. Do not name real
places, agencies or companies.
