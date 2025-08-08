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
import xml.etree.ElementTree as ET
import re

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
        
        print(f"検索クエリ: {query}")
        print(f"検索期間: {date_from} から {date_to}")
        
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": 100,  # 最大100件に増やす
            "retmode": "json",
            "sort": "pub_date"
        }
        
        response = requests.get(f"{self.base_url}esearch.fcgi", params=params)
        data = response.json()
        
        pmid_list = data.get("esearchresult", {}).get("idlist", [])
        print(f"見つかった論文数: {len(pmid_list)}件")
        print(f"PMID リスト: {pmid_list[:10]}...")  # 最初の10件を表示
        
        return pmid_list
    
    def fetch_article_details(self, pmid_list: List[str]) -> List[Dict]:
        """論文の詳細情報を取得"""
        if not pmid_list:
            return []
        
        articles = []
        
        # PMIDを一度に最大20件ずつ処理
        batch_size = 20
        for i in range(0, len(pmid_list), batch_size):
            batch = pmid_list[i:i+batch_size]
            print(f"バッチ {i//batch_size + 1}: {len(batch)}件の論文を取得中...")
            
            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml"
            }
            
            response = requests.get(f"{self.base_url}efetch.fcgi", params=params)
            
            # XMLパース
            try:
                root = ET.fromstring(response.content)
                
                # 各PubmedArticleを処理
                for article_elem in root.findall('.//PubmedArticle'):
                    article_data = self._parse_article_element(article_elem)
                    if article_data:
                        articles.append(article_data)
                        print(f"  論文取得: {article_data['title'][:50]}...")
                        
            except ET.ParseError as e:
                print(f"XMLパースエラー: {e}")
                continue
            
            # API制限対策
            if i + batch_size < len(pmid_list):
                time.sleep(0.5)
        
        # 重複を除去（PMIDでユニーク化）
        unique_articles = {}
        for article in articles:
            if article['pmid'] not in unique_articles:
                unique_articles[article['pmid']] = article
        
        final_articles = list(unique_articles.values())
        print(f"最終的な論文数: {len(final_articles)}件（重複除去後）")
        
        return final_articles
    
    def _parse_article_element(self, article_elem) -> Dict:
        """XML要素から論文情報を抽出"""
        try:
            # PMID取得
            pmid_elem = article_elem.find('.//PMID')
            if pmid_elem is None:
                return None
            pmid = pmid_elem.text
            
            # タイトル取得
            title_elem = article_elem.find('.//ArticleTitle')
            title = title_elem.text if title_elem is not None else "タイトルなし"
            
            # アブストラクト取得
            abstract_texts = []
            abstract_elems = article_elem.findall('.//AbstractText')
            for abs_elem in abstract_elems:
                if abs_elem.text:
                    abstract_texts.append(abs_elem.text)
                # Labelがある場合（構造化アブストラクト）
                if 'Label' in abs_elem.attrib:
                    label = abs_elem.attrib['Label']
                    text = abs_elem.text or ""
                    abstract_texts.append(f"{label}: {text}")
            abstract = " ".join(abstract_texts)
            
            # 雑誌名取得
            journal_elem = article_elem.find('.//Journal/Title')
            journal = journal_elem.text if journal_elem is not None else "雑誌名不明"
            
            # 著者名取得（最大3名）
            authors = []
            author_elems = article_elem.findall('.//Author')
            for author_elem in author_elems[:3]:
                lastname = author_elem.find('LastName')
                forename = author_elem.find('ForeName')
                if lastname is not None and forename is not None:
                    authors.append(f"{forename.text} {lastname.text}")
            
            if len(author_elems) > 3:
                authors.append("et al.")
            author_str = ", ".join(authors) if authors else "著者不明"
            
            # 発行日取得
            pub_date = self._extract_pub_date(article_elem)
            
            # DOI取得
            doi = ""
            for id_elem in article_elem.findall('.//ArticleId'):
                if id_elem.get('IdType') == 'doi':
                    doi = id_elem.text
                    break
            
            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": author_str,
                "journal": journal,
                "pub_date": pub_date,
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            }
            
        except Exception as e:
            print(f"論文パースエラー: {e}")
            return None
    
    def _extract_pub_date(self, article_elem) -> str:
        """発行日を抽出"""
        # PubDateを優先的に使用
        pubdate = article_elem.find('.//PubDate')
        if pubdate is not None:
            year = pubdate.find('Year')
            month = pubdate.find('Month')
            day = pubdate.find('Day')
            
            year_str = year.text if year is not None else "2024"
            month_str = month.text if month is not None else "01"
            day_str = day.text if day is not None else "01"
            
            # 月名を数字に変換
            month_map = {
                'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
                'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
                'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
            }
            if month_str in month_map:
                month_str = month_map[month_str]
            
            return f"{year_str}/{month_str}/{day_str}"
        
        return "日付不明"

class AIReporter:
    """Google Gemini APIを使用してAI要約を生成"""
    
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
    
    def summarize_abstract(self, abstract: str, title: str) -> List[str]:
        """アブストラクトを日本語で要約"""
        if not abstract or len(abstract) < 50:
            return [
                "・要約対象のアブストラクトが不十分です",
                "・原文をご確認ください",
                "・詳細情報は論文本文を参照",
                "・PubMedリンクから全文アクセス可能"
            ]
        
        prompt = f"""
        以下の医学論文のアブストラクトを読んで、最も重要なポイントを日本語で4点に要約してください。
        各ポイントは簡潔に、専門用語は適切に日本語訳してください。
        
        論文タイトル: {title}
        
        アブストラクト:
        {abstract[:3000]}  # 文字数制限
        
        出力形式（必ず以下の形式で4点出力）:
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
                while len(points) < 4:
                    points.append("・詳細はアブストラクトを参照してください")
                return points[:4]
                
        except Exception as e:
            print(f"要約エラー ({title[:30]}...): {e}")
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
        # 日本時間で表示
        from datetime import timezone, timedelta as td
        jst = timezone(td(hours=9))
        now_jst = datetime.now(jst)
        
        subject = f"【PubMed新着論文】{field_name} - {now_jst.strftime('%Y年%m月%d日')}"
        
        # メール本文の構築
        body = f"""新着論文AI要約配信 {field_name}
配信日時：{now_jst.strftime('%Y年%m月%d日 %H時%M分')}

本日の新着論文は{len(articles)}件です。

"""
        # 最新の論文から表示（最大20件）
        for i, article in enumerate(articles[:20], 1):
            body += f"""[論文{i}]
原題：{article['title']}
著者：{article['authors']}
雑誌名：{article['journal']}
発行日：{article['pub_date']}
PubMed：{article['url']}
DOI：https://doi.org/{article['doi']} (DOI: {article['doi']})

要約（AI生成）：
{chr(10).join(article.get('summary', ['要約なし']))}

---
"""
        
        if len(articles) > 20:
            body += f"\n※ 他{len(articles)-20}件の論文があります。PubMedで直接ご確認ください。\n"
        
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
        "Clinical and Translational Radiation Oncology",
        "Practical Radiation Oncology",
        "Advances in Radiation Oncology"
    ]
    
    print("=== PubMed論文収集開始 ===")
    
    # 1. PubMedから新着論文を取得
    fetcher = PubMedFetcher(JOURNAL_NAMES)
    pmid_list = fetcher.search_articles(days_back=7)  # 過去7日分
    
    if not pmid_list:
        print("新着論文はありません")
        # 新着なしでもメール通知
        sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        sender.send_summary(TO_EMAIL, [], "放射線腫瘍学")
        return
    
    print(f"\n{len(pmid_list)}件の論文IDを取得")
    
    # 2. 論文詳細を取得
    articles = fetcher.fetch_article_details(pmid_list)
    print(f"\n{len(articles)}件の論文詳細を取得完了")
    
    # 3. AI要約を生成
    if articles:
        print("\n=== AI要約生成開始 ===")
        summarizer = AIReporter(GEMINI_API_KEY)
        
        for idx, article in enumerate(articles, 1):
            print(f"要約中 ({idx}/{len(articles)}): {article['title'][:50]}...")
            time.sleep(1)  # API制限対策
            article['summary'] = summarizer.summarize_abstract(
                article['abstract'], 
                article['title']
            )
    
    # 4. メール送信
    if articles:
        print("\n=== メール送信 ===")
        sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        sender.send_summary(TO_EMAIL, articles, "放射線腫瘍学")
        print(f"✅ 要約メールを送信しました: {len(articles)}件")
    
    print("\n=== 処理完了 ===")

if __name__ == "__main__":
    main()
