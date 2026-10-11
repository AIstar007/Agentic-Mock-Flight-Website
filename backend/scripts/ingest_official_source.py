#!/usr/bin/env python3
"""Fetch a single HTTPS official source into a review file; never auto-approve it.

Usage: python scripts/ingest_official_source.py https://www.example.gov/page --title "Page title" --jurisdiction Country
The output is a draft JSON document under data/source_drafts. Review the rule wording,
current date, applicability and citations before adding it to knowledge.json/SQLite.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_SUFFIXES=(".gov", ".gov.uk", ".gov.au", ".gov.in", ".gov.ae", ".gc.ca", ".europa.eu", ".icao.int", ".iata.org", ".emirates.com", ".faa.gov", ".caa.co.uk", ".govt.nz")

class Text(HTMLParser):
 def __init__(self): super().__init__(); self.skip=0; self.parts=[]; self.title=False; self.title_parts=[]
 def handle_starttag(self,tag,attrs):
  if tag in {"script","style","nav","footer","header","svg","form"}: self.skip+=1
  if tag=="title": self.title=True
  if tag in {"p","li","h1","h2","h3","h4","br"}: self.parts.append("\n")
 def handle_endtag(self,tag):
  if tag in {"script","style","nav","footer","header","svg","form"} and self.skip: self.skip-=1
  if tag=="title": self.title=False
  if tag in {"p","li","h1","h2","h3","h4","br"}: self.parts.append("\n")
 def handle_data(self,data):
  if self.title and data.strip(): self.title_parts.append(data.strip())
  if not self.skip and data.strip(): self.parts.append(data.strip()+" ")

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('url'); ap.add_argument('--title',required=True); ap.add_argument('--jurisdiction',required=True); ap.add_argument('--category',default='aviation_policy'); args=ap.parse_args()
 u=urlparse(args.url); host=(u.hostname or '').lower()
 if u.scheme!='https' or not host or not any(host==s or host.endswith('.'+s) for s in ALLOWED_SUFFIXES):
  ap.error('URL must use HTTPS and an approved official-source hostname suffix. Edit the script allowlist after security review if adding a regulator.')
 req=Request(args.url,headers={'User-Agent':'AirlinePolicyPOC/1.0 (+source review required)','Accept':'text/html,application/xhtml+xml'})
 with urlopen(req,timeout=12) as resp:
  final=resp.geturl(); body=resp.read(1_500_000).decode('utf-8','replace')
 if urlparse(final).scheme!='https' or (urlparse(final).hostname or '').lower()!=host: ap.error('Refused a redirect to another host')
 parser=Text(); parser.feed(body); content=re.sub(r'[ \t]+',' ',''.join(parser.parts)); content=re.sub(r'\n\s*\n+', '\n', content).strip()[:12000]
 if len(content)<150: ap.error('Page yielded too little readable content; add a PDF extractor for the specific official document instead.')
 doc={'id':'SRC_'+hashlib.sha256(args.url.encode()).hexdigest()[:16].upper(),'title':args.title,'category':args.category,'jurisdiction':args.jurisdiction,'source_type':'official_public_webpage','source_url':args.url,'verified_at':date.today().isoformat(),'content':content,'brand_scope':'global_aviation','approved_for_use':False,'review_status':'DRAFT_REQUIRES_POLICY_REVIEW'}
 out=Path(__file__).resolve().parents[1]/'data'/'source_drafts'; out.mkdir(parents=True,exist_ok=True); target=out/(doc['id']+'.json'); target.write_text(json.dumps(doc,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'draft':str(target),'characters':len(content),'approved_for_use':False,'message':'Review the live source and rule applicability before adding to the approved corpus.'},indent=2))
if __name__=='__main__': main()
