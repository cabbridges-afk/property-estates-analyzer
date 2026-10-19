Present & Future Estates Analyzer
=================================

Run locally
-----------
This version uses a local Python proxy so API keys never enter the browser or
the HTML source file.

1. In a terminal, move to this folder:
   cd /Users/charlottebridges

2. Set the OpenRouter key for the current terminal session:
   export OPENROUTER_API_KEY="your-key"

   Optional: select a different OpenRouter model:
   export OPENROUTER_MODEL="openai/gpt-4.1-mini"

3. Optional but recommended: configure a CourtListener token for more reliable
   rate limits:
   export COURTLISTENER_API_TOKEN="your-token"

4. Start the local server:
   python3 rap_server.py

5. Open http://127.0.0.1:8765/estates_analyzer.html

Security
--------
- Never paste API keys into the app or commit them to a file.
- rap_server.py reads OPENROUTER_API_KEY and COURTLISTENER_API_TOKEN only from
  its process environment.
- The app communicates only with the local proxy. The proxy listens at
  127.0.0.1, not on the network.

AI RAP memorandum
-----------------
After each deterministic estate analysis, the page asks the local proxy for a
common-law Rule Against Perpetuities study memorandum. The prompt requires
JSON, uses temperature 0, and includes the hypothetical plus the deterministic
classifications. The app treats the memorandum as a study aid, not a legal
opinion. The rule engine remains independent of the model output.

Analogous cases
---------------
For each analysis the proxy sends a bounded doctrine-oriented query to
CourtListener v4's opinion search and renders no more than three results.
Each card links to CourtListener and includes CourtListener-provided metadata
and a short returned snippet. Live search can be rate limited or return weak
analogies; those results should be validated in the opinion itself.

Validation
----------
Run the proxy unit tests:
  python3 -m unittest -v test_rap_server.py

The browser app includes its deterministic-rule regression tests under
"Run self-tests".
