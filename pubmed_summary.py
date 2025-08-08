import os
import json
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict
import google.generativeai as genai
import time

class PubMedFetcher:
    """PubMed APIを使用して論文を取得"""
    
    def __init__(self, journal_names: List[str]):
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        self.journal_names = journal_names
        
    def search_articles(self, days_back: int = 1) -> List[str]:
        """指定日数以内の論文IDを取得"""
        date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
        date_to = datetime.now().strftime("%Y/%m/%d")
        
        # 雑誌名でクエリを構築
        journal_query = " OR ".join([f'"{journal}"[Journal]' for journal in self.journal_names])
        query = f"({journal_query}) AND {date_from}:{date_to}[PDAT]"
        
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": 20,  # 最大20件
            "retmode": "json",
            "sort": "pub_date"
        }
        
        response = requests.get(f"{self.base_url}esearch.fcgi", params=params)
        data = response.json()
        
        return data.get("esearchresult", {}).get("idlist", [])
    
    def fetch_article_details(self, pmid_list: List[str]) -> List[Dict]:
        """論文の詳細情報を取得"""
        if not pmid_list:
            return []
        
        params = {
            "db": "pubmed",
            "id": ",".join(pmid_list),
            "retmode": "xml"
        }
        
        response = requests.get(f"{self.base_url}efetch.fcgi", params=params)
        
        # XMLパースの代わりに簡易的なテキスト処理
        articles = []
        content = response.text
        
        for pmid in pmid_list:
            article = self._parse_article(content, pmid)
            if article:
                articles.append(article)
        
        return articles
    
    def _parse_article(self, xml_content: str, pmid: str) -> Dict:
        """XMLから論文情報を抽出（簡易版）"""
        import re
        
        # PMID周辺の情報を抽出
        pattern = f'<PubmedArticle>.*?<PMID.*?>{pmid}</PMID>.*?</PubmedArticle>'
        match = re.search(pattern, xml_content, re.DOTALL)
        
        if not match:
            return None
        
        article_xml = match.group()
        
        # 各フィールドを抽出
        title = self._extract_field(article_xml, "ArticleTitle")
        abstract = self._extract_field(article_xml, "AbstractText")
        journal = self._extract_field(article_xml, "Title")  # Journal Title
        
        # 著者名を抽出
        authors = re.findall(r'<LastName>(.*?)</LastName>.*?<ForeName>(.*?)</ForeName>', 
                           article_xml, re.DOTALL)
        author_names = [f"{fn} {ln}" for ln, fn in authors[:3]]  # 最初の3名
        
        # 発行日を抽出
        year = self._extract_field(article_xml, "Year") or "2024"
        month = self._extract_field(article_xml, "Month") or "01"
        day = self._extract_field(article_xml, "Day") or "01"
        
        # DOIを抽出
        doi_match = re.search(r'<ArticleId IdType="doi">(.*?)</ArticleId>', article_xml)
        doi = doi_match.group(1) if doi_match else ""
        
        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(author_names) + (" et al." if len(authors) > 3 else ""),
            "journal": journal,
            "pub_date": f"{year}/{month}/{day}",
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        }
    
    def _extract_field(self, xml: str, tag: str) -> str:
        """XMLからフィールドを抽出"""
        import re
        pattern = f'<{tag}.*?>(.*?)</{tag}>'
        match = re.search(pattern, xml, re.DOTALL)
        if match:
            # HTMLタグを除去
            text = re.sub(r'<[^>]+>', '', match.group(1))
            return text.strip()
        return ""

class AIReporter:
    """Google Gemini APIを使用してAI要約を生成"""
    
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
    
    def summarize_abstract(self, abstract: str, title: str) -> List[str]:
        """アブストラクトを日本語で要約"""
        if not abstract:
            return ["要約対象のアブストラクトがありません"]
        
        prompt = f"""
        以下の医学論文のアブストラクトを読んで、放射線腫瘍学の専門家向けに、重要なポイントを日本語で4つの箇条書きで日本語に要約してください。
        
        論文タイトル: {title}
        
        アブストラクト:
        {abstract}
        
        出力形式:
        ・[ポイント1]
        ・[ポイント2]
        ・[ポイント3]
        ・[ポイント4]
        """
        
        try:
            response = self.model.generate_content(prompt)
            # 箇条書きを抽出
            lines = response.text.strip().split('\n')
            points = [line.strip() for line in lines if line.strip().startswith('・')]
            
            if len(points) >= 4:
                return points[:4]
            else:
                # 不足分を補完
                return points + ["詳細はアブストラクトを参照してください"] * (4 - len(points))
        except Exception as e:
            print(f"要約エラー: {e}")
            return [
                "・AIによる要約生成に失敗しました",
                "・原文をご確認ください",
                "・一時的なエラーの可能性があります",
                "・後ほど再試行してください"
            ]

class EmailSender:
    """Gmail SMTPを使用してメール送信"""
    
    def __init__(self, gmail_address: str, app_password: str):
        self.gmail_address = gmail_address
        self.app_password = app_password
    
    def send_summary(self, to_email: str, articles: List[Dict], field_name: str = "放射線腫瘍学"):
        """要約メールを送信"""
        subject = f"【PubMed新着論文】{field_name} - {datetime.now().strftime('%Y年%m月%d日')}"
        
        # メール本文の構築
        body = f"""新着論文AI要約配信 {field_name}

本日の新着論文は{len(articles)}件です。

"""
        for i, article in enumerate(articles, 1):
            body += f"""[論文{i}]
原題：{article['title']}
著者：{article['authors']}
雑誌名：{article['journal']}
発行日：{article['pub_date']}
PubMed：{article['url']}
DOI：https://doi.org/{article['doi']} (DOI: {article['doi']})

要約（AI生成）：
{chr(10).join(article['summary'])}

---
"""
        
        # メール送信
        msg = MIMEMultipart()
        msg['From'] = self.gmail_address
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        try:
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
            print(f"メール送信成功: {to_email}")
        except Exception as e:
            print(f"メール送信エラー: {e}")
            raise

def main():
    """メイン処理"""
    # 環境変数から設定を取得
    GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS')
    GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    TO_EMAIL = os.environ.get('TO_EMAIL')
    
    # 監視する雑誌名（放射線腫瘍学の主要誌）
    JOURNAL_NAMES = [
        "International Journal of Radiation Oncology Biology Physics",
        "Radiotherapy and Oncology",
        "Journal of Radiation Research",
        "Radiation Oncology",
        "Clinical and Translational Radiation Oncology"
    ]
    
    # 1. PubMedから新着論文を取得
    fetcher = PubMedFetcher(JOURNAL_NAMES)
    pmid_list = fetcher.search_articles(days_back=1)  # 過去7日分
    
    if not pmid_list:
        print("新着論文はありません")
        return
    
    print(f"{len(pmid_list)}件の新着論文を発見")
    
    # 2. 論文詳細を取得
    articles = fetcher.fetch_article_details(pmid_list)
    
    # 3. AI要約を生成
    summarizer = AIReporter(GEMINI_API_KEY)
    for article in articles:
        time.sleep(1)  # API制限対策
        article['summary'] = summarizer.summarize_abstract(
            article['abstract'], 
            article['title']
        )
    
    # 4. メール送信
    if articles:
        sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        sender.send_summary(TO_EMAIL, articles)
        print(f"要約メールを送信しました: {len(articles)}件")

if __name__ == "__main__":
    main()