Miami-Dade County Service IQ Review

I want to break down what is going right and what is going wrong with Miami-Dade so we can actually move this forward.

Right now Miami-Dade is showing 10 leads.

The issue is that the system is mostly identifying surplus based on auction data only. That is not enough for our business.

Seeing that a property sold for more than what was owed is only step one.

That does not tell us if the lead is pursuable.

The whole reason we wanted this scraper built was to remove the human element. But if the system only shows:

1. Sale price
2. Amount owed
3. Apparent surplus
4. Auction-only evidence
5. A source link

Then a human still has to go do the real research.

That means the scraper is not doing what we actually need it to do.

The tiers and lead status need to be determined based on the docket scrape and document review, not just the auction math.

Example:

One Miami-Dade mortgage foreclosure showed an apparent surplus of $29,676.

It sold for $58,100 and the amount owed was around $28,000.

At first glance, that looks like a valid surplus lead.

But when I searched the Miami-Dade case search using the year, sequence number, and case code from the auction calculator, the docket showed:

1. Motion for surplus funds
2. Owner's claim for mortgage foreclosure surplus

That means someone already filed a claim.

So that lead should not be marked as valid or pursuable.

This is exactly why the docket scrape is required.

A lead cannot be validated by auction data alone.

For Miami-Dade mortgage foreclosures, the scraper needs to:

1. Pull the case number details from the auction source
2. Separate the year, sequence number, and case code
3. Search Miami-Dade local case search
4. Open the docket
5. Scan for claim activity
6. Scan for anything that could kill or delay the sale
7. Update the lead status based on the docket

The scraper needs to detect terms like:

1. Motion for surplus funds
2. Owner's claim for surplus
3. Claim for mortgage foreclosure surplus
4. Order disbursing surplus
5. Motion to vacate sale
6. Sale cancelled
7. Sale set aside
8. Bankruptcy
9. Suggestion of bankruptcy

We also need a 3-day, 5-day, and 14-day recheck sequence after the sale because claims and docket updates can appear after the auction.

Now on the tax deed side, the current link is incorrect.

For Miami-Dade tax deed sales, the docket link should not go to the mortgage foreclosure case search.

Tax deed sales need to route to RealtyTM, which is the Miami-Dade tax deed portal.

That is where the tax deed report and documents are found.

For tax deed leads, the scraper needs to:

1. Identify that the lead is a tax deed sale
2. Route to RealtyTM instead of the mortgage foreclosure case search
3. Search the tax deed case number
4. Open the tax deed report
5. Review the documents tab
6. Check for claims
7. Check for notice of surplus letter
8. Review the Property Information Report or title search document
9. Identify liens, mortgages, HOA liens, judgments, or other encumbrances

Example:

One Miami-Dade tax deed lead showed a surplus of $374,856.

Auction data alone made it look like a strong lead.

But once I opened RealtyTM and reviewed the documents, the Property Information Report showed liens.

There was a condominium association lien and another lien that could impact the surplus.

That does not automatically kill the lead, but it absolutely needs to be flagged.

At minimum, the AI should say:

Lien identified.

If the scraper can go further, it should pull the book and page number and use the Miami-Dade recorder search to review the recorded lien amount.

For example, one lien showed a book and page reference, and after searching the recorder, the lien amount was $3,581.

That would be valuable information to have inside the lead record.

So the required Miami-Dade logic should be:

Mortgage Foreclosure Leads

1. Use Miami-Dade case search
2. Search by year, sequence, and case code
3. Scrape the docket
4. Identify whether any claim has already been filed
5. Identify any motion to vacate, bankruptcy, cancellation, or sale issue
6. Update evidence level and lead status based on docket results

Tax Deed Leads

1. Use RealtyTM
2. Search the tax deed case
3. Open the documents tab
4. Review the tax deed report
5. Review the Property Information Report or title search document
6. Identify liens, mortgages, HOA liens, judgments, and claims
7. Flag the lead accordingly

Evidence Level should not just say "auction only."

We need better evidence levels like:

1. Auction only
2. Docket checked
3. No claim found
4. Claim filed
5. Title report reviewed
6. Lien identified
7. Bankruptcy found
8. Sale issue found
9. Not pursuable
10. Pursuable with caution
11. Pursuable

We also need to add the foreclosure type to every lead.

The lead needs to clearly say:

1. Mortgage foreclosure
2. Tax deed sale

This matters because each one uses a different county source and a different validation process.

PropertyRadar should only be used as enrichment.

PropertyRadar can help us identify second positions, HOA liens, HUD loans, or other liens, but the county scraper is what needs to confirm:

1. Has a claim been filed?
2. Has there been a motion to vacate?
3. Has there been a bankruptcy?
4. Has the sale been cancelled?
5. Are there docket updates that change the lead status?

Also, when we download the CSV, the format needs to match our lead database.

Recommended fields:

1. County
2. Foreclosure type
3. Case number
4. Sale date
5. Sale price
6. Judgment amount or amount owed
7. Apparent surplus
8. Owner name
9. Property address
10. Third-party bidder status
11. Docket source
12. Docket scrape status
13. Last docket check date
14. Claim filed
15. Claim date
16. Claim type
17. Bankruptcy found
18. Motion to vacate found
19. Sale issue found
20. Title report reviewed
21. Liens identified
22. Lien type
23. Lien amount if available
24. Book and page if available
25. Evidence level
26. Lead status
27. Notes

Bottom line:

The Miami-Dade leads are mostly valid from an auction perspective, except the one where a surplus claim has already been filed.

But auction validation is not enough.

The system needs to scrape and review the correct docket or document source based on the foreclosure type.

A link to the source is not the same thing as a docket scrape.

The scraper needs to actually review the docket, classify the lead, update the evidence level, and determine whether the lead is pursuable.

That was part of the original scope, and it is the piece that makes this useful for our business.
