# stockpriceRec

Yahooファイナンスの株価を、自分のPCの中へ貯めていくツールです。

Yahooが返せる本数には足ごとに上限があります。ここでは取得できた足をSQLiteに残し、次の実行では続きだけを足していきます。同じ時刻の足は上書きするので、取引時間中の未確定の値も、あとから確定値に置き換わります。

## Yahooが返す期間

| 足 | 1回の取得で残せる期間の目安 |
| --- | --- |
| 1分 | 約13日 |
| 2分 | 約45日 |
| 5分・15分・30分・90分 | 約90日 |
| 1時間 | 約2年 |
| 日足・週足・月足 | 上場来 |

日足で `range=max` のように広い指定をすると、月足へ間引かれることがあります。このツールは日足・週足・月足を期間指定で取り、間引かれた応答は分割して取り直します。分足は、Yahooがまとめて返す最大幅で取ります。

その上限より短い間隔で取り続けると、Yahoo側から消えた時刻も手元に残ります。日足は、何日空いても次の取得で埋まります。

## 準備

Python 3.11 以上があれば動きます。追加のライブラリは不要です。

```bash
python -m stockrec --help
```

## 使い方

東証の4桁コードは `7203.T` に揃えます。`AAPL` や `7203.T`、`^N225` のように市場まで書いてあるコードはそのまま使います。

```bash
python -m stockrec add 7203 9984 AAPL
python -m stockrec update --interval 1d --interval 1m --interval 5m
python -m stockrec list
python -m stockrec show 7203 --last 20
python -m stockrec show 7203 --interval 1m --from 2026-09-01 --to 2026-09-24
python -m stockrec export 7203 --interval 1d -o toyota.csv
python -m stockrec export --all --interval 1d -o all.csv
```

`update` の銘柄を省略すると、`watchlist.txt` にある銘柄を取ります。このファイルがまだ無いときだけ、データベースに登録済みの銘柄を取ります。足を省略すると日足だけです。

```bash
python -m stockrec remove 7203
python -m stockrec remove 7203 --purge
```

`remove` は監視リストから外すだけです。保存済みの価格は残します。価格ごと消すときは `--purge` を付けます。

## 東証の全銘柄

JPXが公表している上場一覧（プライム、スタンダード、グロース、ETF、REIT、PRO Market、優先株）を読み、全銘柄の日足を保存します。

```bash
python -m stockrec jpx
python -m stockrec show 7203
```

一覧は `data/jpx/listings.csv`、日足は `data/jpx/daily/YYYY-MM-DD.csv` です。1銘柄1行で、同じ日のファイルは取り直すたびに上書きします。直近20日を毎回取り直すので、休場日や取得漏れがあとから埋まります。

全銘柄の分足は対象にしていません。約4400銘柄の分足を毎日残すと、取得が終わらず、GitHubに置けるサイズもすぐに超えます。分足が必要な銘柄だけ `watchlist.txt` に書き、`update --interval 1m` で個別に残します。

## 自動実行して Git に残す

PCを起動したままにしなくても、GitHub Actions が平日 16:30（日本時間）に `jpx` を実行し、`data/jpx` をこのリポジトリへコミットします。価格が変わったときだけコミットします。混雑しているときは開始が遅れることがあります。

スケジュールは `main` に入ったあとで動きます。初回は Actions タブの「Update prices」からも実行できます。

`data/prices.db` は手元で `show` するためのデータベースで、Git には入れません。Git に残る価格は `data/jpx` のCSVです。

穴を開けない間隔は、1分足が12日より短いこと、5分足が90日より短いことです。日足は何日空いても、次の取得で埋まります。全銘柄の日足は、Yahooが過去分も返すため、20日分を毎回取り直しています。

前回の分足取得から上限以上の時間が空くと、そのあいだは Yahoo がもう返さないため埋まりません。`update` はその旨を表示します。

取引時間中の途中経過も残すときは、市場が開いているあいだ次を動かします。止めるときは Ctrl+C です。この実行は Git には送りません。

```bash
python -m stockrec watch --interval 1m --every 60
```

## 手元で実行する

同じ取得を自分の PC で行うコマンドは `run_update.sh` と `run_update.bat` に入れてあります。Windows のタスクスケジューラなら `run_update.bat` を平日 16:30 に、cron なら次です。7:30 UTC は日本時間の 16:30 です。

```
30 7 * * 1-5 /path/to/stockpriceRec/run_update.sh
```

## 保存先

既定のファイルは、コマンドを実行したディレクトリの `data/prices.db` です。`--db` か環境変数 `STOCKREC_DB` で場所を変えられます。監視銘柄は `watchlist.txt`（環境変数 `STOCKREC_WATCHLIST`）です。

CSV は Excel で文字化けしないよう、UTF-8（BOM 付き）で書き出します。日足の時刻は取引所の現地日付、分足は足の開始時刻です。

## 注意

取得先は Yahooファイナンスの chart API です。相場ページと同じ非公式の口で、個人の記録用です。Yahoo の仕様が変わると取れなくなることがあります。売買の判断には、証券会社など公式の情報を使ってください。
