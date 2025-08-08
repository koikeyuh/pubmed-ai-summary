import os
import json
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Set
import google.generativeai as genai
import time
import xml.etree.ElementTree as ET
import re

# 雑誌名の略称辞書
JOURNAL_ABBREVIATIONS = {
    "International Journal of Radiation Oncology Biology Physics": "Int J Radiat Oncol Biol Phys",
    "Radiotherapy and Oncology": "Radiother Oncol",
    "Journal of Radiation Research": "J Radiat Res",
    "Radiation Oncology": "Radiat Oncol",
    "Clinical and Translational Radiation Oncology": "Clin Transl Radiat Oncol",
    "Practical Radiation Oncology": "Pract Radiat Oncol",
    "Advances in Radiation Oncology": "Adv Radiat Oncol",
    "International Journal of Radiation Biology": "Int J Radiat Biol",
    "Radiation Research": "Radiat Res",
    "Medical Physics": "Med Phys",
    "Physics in Medicine and Biology": "Phys Med Biol",
    "Strahlentherapie und Onkologie": "Strahlenther Onkol",
    "Journal of Applied Clinical Medical Physics": "J Appl Clin Med Phys",
    "Cancer/Radiotherapie": "Cancer Radiother",
    "Seminars in Radiation Oncology": "Semin Radiat Oncol",
    "Brachytherapy": "Brachytherapy",
    "Reports of Practical Oncology and Radiotherapy": "Rep Pract Oncol Radiother",
    "Journal of Radiation Oncology": "J Radiat Oncol",
    "Radiation and Environmental Biophysics": "Radiat Environ Biophys",
    "Radiation Protection Dosimetry": "Radiat Prot Dosimetry"
}

def get_journal_abbreviation(journal_name: str) -> str:
    """雑誌名を略称に変換"""
    if journal_name in JOURNAL_ABBREVIATIONS:
        return JOURNAL_ABBREVIATIONS[journal_name]
    
    for full_name, abbrev in JOURNAL_ABBREVIATIONS.items():
        if full_name.lower() in journal_name.lower():
            return abbrev
    
    return journal_name

class HistoryManager:
    """送信履歴を管理するクラス"""
    
    def __init__(self, history_file: str = "sent_articles_history.json"):
        self.history_file = history_file
        self.sent_pmids = self.load_history()
    
    def load_history(self) -> Set[str]:
        """履歴ファイルから送信済みPMIDを読み込み"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    # 古いエントリを削除（90日以上前）
                    cutoff_date = (datetime.now() - timedelta(days=90)).isoformat()
                    filtered_data = {
                        pmid: date for pmid, date in data.items()
                        if date > cutoff_date
                    }
                    return set(filtered_data.keys())
            except Exception as e:
                print(f"履歴ファイル読み込みエラー: {e}")
                return set()
        return set()
    
    def is_sent(self, pmid: str) -> bool:
        """PMIDが送信済みかチェック"""
        return pmid in self.sent_pmids
    
    def add_sent_articles(self, pmids: List[str]):
        """送信済みPMIDを追加"""
        current_date = datetime.now().isoformat()
        
        # 既存の履歴を読み込み
        history_data = {}
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    history_data = json.load(f)
            except:
                pass
        
        # 新しいPMIDを追加
        for pmid in pmids:
            history_data[pmid] = current_date
            self.sent_pmids.add(pmid)
        
        # 90日以上前のエントリを削除
        cutoff_date = (datetime.now() - timedelta(days=90)).isoformat()
        history_data = {
            pmid: date for pmid, date in history_data.items()
            if date > cutoff_date
        }
        
        # ファイルに保存
        with open(self.history_file, 'w') as f:
            json.dump(history_data, f, indent=2)
        
        print(f"履歴を更新: {len(pmids)}件のPMIDを記録")
    
    def get_stats(self) -> Dict:
        """統計情報を取得"""
        if os.path.exists(self.history_file):
            with open(self.history_file, 'r') as f:
                data = json.load(f)
                return {
                    "total_sent": len(data),
                    "oldest_date": min(data.values()) if data else None,
                    "newest_date": max(data.values()) if data else None
                }
        return {"total_sent": 0, "oldest_date": None, "newest_date": None}

class PubMedFetcher:
    """PubMed APIを使用して論文を取得"""
    
    def __init__(self, journal_names: List[str], history_manager: HistoryManager = None):
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        self.journal_names = journal_names
        self.history_manager = history_manager
        
    def search_articles(self, days_back: int = 1) -> List[str]:
        """指定日数以内の論文IDを取得"""
        date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
        date_to = datetime.now().strftime("%Y/%m/%d")
        
        journal_query = " OR ".join([f'"{journal}"[Journal]' for journal in self.journal_names])
        query = f"({journal_query}) AND {date_from}:{date_to}[PDAT]"
        
        print(f"検索クエリ: {query}")
        print(f"検索期間: {date_from} から {date_to}")
        
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": 100,
            "retmode": "json",
            "sort": "pub_date"
        }
        
        response = requests.get(f"{self.base_url}esearch.fcgi", params=params)
        data = response.json()
        
        all_pmids = data.get("esearchresult", {}).get("idlist", [])
        
        # 履歴フィルタリング
        if self.history_manager:
            new_pmids = [pmid for pmid in all_pmids if not self.history_manager.is_sent(pmid)]
            filtered_count = len(all_pmids) - len(new_pmids)
            
            print(f"見つかった論文数: {len(all_pmids)}件")
            print(f"既送信でスキップ: {filtered_count}件")
            print(f"新規論文数: {len(new_pmids)}件")
            
            return new_pmids
        else:
            print(f"見つかった論文数: {len(all_pmids)}件")
            return all_pmids
    
    def fetch_article_details(self, pmid_list: List[str]) -> List[Dict]:
        """論文の詳細情報を取得"""
        if not pmid_list:
            return []
        
        articles = []
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
            
            try:
                root = ET.fromstring(response.content)
                
                for article_elem in root.findall('.//PubmedArticle'):
                    article_data = self._parse_article_element(article_elem)
                    if article_data:
                        articles.append(article_data)
                        print(f"  論文取得: {article_data['title'][:50]}...")
                        
            except ET.ParseError as e:
                print(f"XMLパースエラー: {e}")
                continue
            
            if i + batch_size < len(pmid_list):
                time.sleep(0.5)
        
        # 重複を除去
        unique_articles = {}
        for article in articles:
            if article['pmid'] not in unique_articles:
                unique_articles[article['pmid']] = article
        
        final_articles = list(unique_articles.values())
        print(f"最終的な論文数: {len(final_articles)}件")
        
        return final_articles
    
    def _parse_article_element(self, article_elem) -> Dict:
        """XML要素から論文情報を抽出"""
        try:
            pmid_elem = article_elem.find('.//PMID')
            if pmid_elem is None:
                return None
            pmid = pmid_elem.text
            
            title_elem = article_elem.find('.//ArticleTitle')
            title = title_elem.text if title_elem is not None else "タイトルなし"
            
            abstract_texts = []
            abstract_elems = article_elem.findall('.//AbstractText')
            for abs_elem in abstract_elems:
                if abs_elem.text:
                    abstract_texts.append(abs_elem.text)
                if 'Label' in abs_elem.attrib:
                    label = abs_elem.attrib['Label']
                    text = abs_elem.text or ""
                    abstract_texts.append(f"{label}: {text}")
            abstract = " ".join(abstract_texts)
            
            # 雑誌名を略称に変換
            journal_elem = article_elem.find('.//Journal/Title')
            journal_full = journal_elem.text if journal_elem is not None else "雑誌名不明"
            journal = get_journal_abbreviation(journal_full)
            
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
            
            pub_date = self._extract_pub_date(article_elem)
            
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
        pubdate = article_elem.find('.//PubDate')
        if pubdate is not None:
            year = pubdate.find('Year')
            month = pubdate.find('Month')
            day = pubdate.find('Day')
            
            year_str = year.text if year is not None else "2024"
            month_str = month.text if month is not None else "01"
            day_str = day.text if day is not None else "01"
            
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
        以下の医学論文のアブストラクトを読んで、放射線腫瘍学の専門家向けに、重要なポイントを日本語で4つの箇条書きで日本語に要約してください。
        
        論文タイトル: {title}
        
        アブストラクト:
        {abstract[:3000]}
        
        出力形式（必ず以下の形式で4点出力）:
        ・[ポイント1]
        ・[ポイント2]
        ・[ポイント3]
        ・[ポイント4]
        """
        
        try:
            response = self.model.generate_content(prompt)
            lines = response.text.strip().split('\n')
            points = [line.strip() for line in lines if line.strip().startswith('・')]
            
            if len(points) >= 4:
                return points[:4]
            else:
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
    
    def send_summary(self, to_email: str, articles: List[Dict], stats: Dict = None, field_name: str = "放射線腫瘍学"):
        """要約メールを送信"""
        from datetime import timezone, timedelta as td
        jst = timezone(td(hours=9))
        now_jst = datetime.now(jst)
        
        subject = f"【PubMed新着論文】{field_name} - {now_jst.strftime('%Y年%m月%d日')}"
        
        body = f"""新着論文AI要約配信 {field_name}
配信日時：{now_jst.strftime('%Y年%m月%d日 %H時%M分')}

本日の新着論文は{len(articles)}件です。
"""
        
        # 統計情報を追加
        if stats:
            body += f"（累計送信論文数：{stats['total_sent']}件）\n"
        
        body += "\n"
        
        if len(articles) == 0:
            body += "本日配信する新着論文はありません。\n"
        else:
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
    GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS')
    GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    TO_EMAIL = os.environ.get('TO_EMAIL')
    
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
    
    # 履歴マネージャーを初期化
    history_manager = HistoryManager()
    stats = history_manager.get_stats()
    print(f"送信履歴: 累計{stats['total_sent']}件の論文を送信済み")
    
    # 履歴ファイルが存在しない場合は空のファイルを作成
    if not os.path.exists("sent_articles_history.json"):
        with open("sent_articles_history.json", 'w') as f:
            json.dump({}, f)
        print("履歴ファイルを初期化しました")
    
    # 1. PubMedから新着論文を取得（履歴フィルタリング付き）
    fetcher = PubMedFetcher(JOURNAL_NAMES, history_manager)
    pmid_list = fetcher.search_articles(days_back=7)
    
    if not pmid_list:
        print("新規論文はありません（すべて送信済みまたは新着なし）")
        sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        sender.send_summary(TO_EMAIL, [], stats, "放射線腫瘍学")
        return  # ここで終了（履歴更新なし）
    
    print(f"\n{len(pmid_list)}件の新規論文を処理")
    
    # 2. 論文詳細を取得
    articles = fetcher.fetch_article_details(pmid_list)
    print(f"\n{len(articles)}件の論文詳細を取得完了")
    
    # 3. AI要約を生成
    if articles:
        print("\n=== AI要約生成開始 ===")
        summarizer = AIReporter(GEMINI_API_KEY)
        
        for idx, article in enumerate(articles, 1):
            print(f"要約中 ({idx}/{len(articles)}): {article['title'][:50]}...")
            time.sleep(1)
            article['summary'] = summarizer.summarize_abstract(
                article['abstract'], 
                article['title']
            )
    
    # 4. メール送信
    print("\n=== メール送信 ===")
    sender = EmailSender(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    sender.send_summary(TO_EMAIL, articles, stats, "放射線腫瘍学")
    
    # 5. 送信履歴を更新（articlesが空でも履歴ファイルは更新）
    if articles:
        sent_pmids = [article['pmid'] for article in articles]
        history_manager.add_sent_articles(sent_pmids)
        print(f"✅ {len(articles)}件の論文を送信し、履歴を更新しました")
    
    print("\n=== 処理完了 ===")

if __name__ == "__main__":
    main()
