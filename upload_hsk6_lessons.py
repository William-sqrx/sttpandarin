"""Upload Standard Course lessons into the flat `words` collection, WITH audio
pre-generated via DashScope qwen-tts. POS prefixes (n., v., adj., etc.) are
stripped from meanings.

Currently uploads:
- HSK 5 Lesson 13: 放眼世界 (Seeing the World)
- HSK 5 Lesson 16: 体重与节食 (Weight and Diet)
- HSK 6 Lesson 2:  父母之爱 (Love of parents)
- HSK 6 Lesson 4:  完美的胜利 (A perfect victory)

Behavior:
- Dry-run (default): preview words, no TTS, no DB writes.
- --apply: for each word NOT already in `words` (matched on chinese+pinyin+level),
  call qwen-tts to synthesize MP3, then insert the doc with audioBlob populated.
- On TTS failure for a single word, it's logged and skipped; other words continue.
- Voice is hardcoded to Cherry. Requires DASHSCOPE_API_KEY env var.

Usage:
    python upload_hsk6_lessons.py                       # preview only
    python upload_hsk6_lessons.py --apply               # generate audio + insert
    python upload_hsk6_lessons.py --apply --throttle 1.5
"""

import argparse
import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from bson import Binary

import db
from clean_pos_prefixes import strip_pos
from tts import MODEL_DEFAULT, TTSError, synthesize_piece_mp3

VOICE = "Cherry"
DASHSCOPE_API_KEY = "sk-7dda280dec2743599ec05424a19c905f"

# ─── HSK 5 Lesson 13: 放眼世界 (Seeing the World) ─────────────────────────────
LESSON_HSK5_L13 = [
    ("锯(子)", "jù(zi)", "v./n. to cut with a saw; saw"),
    ("筐", "kuāng", "n. basket"),
    ("训练", "xùnliàn", "v. to train"),
    ("缺乏", "quēfá", "v. to lack, to be short of"),
    ("项目", "xiàngmù", "n. item, project"),
    ("桃", "táo", "n. peach"),
    ("装", "zhuāng", "v. to load, to hold"),
    ("启发", "qǐfā", "v. to enlighten, to inspire"),
    ("安装", "ānzhuāng", "v. to install, to fix"),
    ("栏杆", "lángān", "n. railing, balustrade"),
    ("甲", "jiǎ", "n. first"),
    ("乙", "yǐ", "n. second"),
    ("工具", "gōngjù", "n. tool, instrument"),
    ("投篮", "tóu lán", "v. to shoot (a basket)"),
    ("踩", "cǎi", "v. to step on, to tread on"),
    ("一再", "yízài", "adv. over and over again"),
    ("重复", "chóngfù", "v. to repeat"),
    ("断断续续", "duànduàn xùxù", "adj. off and on, intermittent"),
    ("激烈", "jīliè", "adj. intense, fierce"),
    ("气氛", "qìfēn", "n. atmosphere"),
    ("何况", "hékuàng", "conj. let alone"),
    ("球迷", "qiúmí", "n. ball game fan"),
    ("工程师", "gōngchéngshī", "n. engineer"),
    ("机器", "jīqì", "n. machine"),
    ("顺畅", "shùnchàng", "adj. smooth, unhindered"),
    ("幼儿园", "yòu'éryuán", "n. kindergarten"),
    ("好奇", "hàoqí", "adj. curious"),
    ("何必", "hébì", "adv. (indicating that there is no need for sth.) why"),
    ("多亏", "duōkuī", "v. luckily, thank to"),
    ("连忙", "liánmáng", "adv. promptly, at once"),
    ("瞧", "qiáo", "v. to look, to see"),
    ("困扰", "kùnrǎo", "v. to trouble, to haunt"),
    ("思维", "sīwéi", "n./v. thinking; to think"),
    ("呆", "dāi", "adj./v. dull, dumb; to stagnate"),
    ("造成", "zàochéng", "v. to cause, to give rise to"),
    ("仿佛", "fǎngfú", "adv. as if; to be like, to be similar to"),
    ("阻碍", "zǔ'ài", "v. to hinder, to impede"),
]

# ─── HSK 5 Lesson 16: 体重与节食 (Weight and Diet) ────────────────────────────
LESSON_HSK5_L16 = [
    ("节食", "jiéshí", "v. to go on a diet"),
    ("报道", "bàodào", "n./v. to report, to cover; report"),
    ("营养", "yíngyǎng", "n. nutrition"),
    ("摄入", "shèrù", "v. to take in, to ingest"),
    ("模式", "móshì", "n. model, pattern"),
    ("波动", "bōdòng", "v. to undulate, to rise and fall"),
    ("总共", "zǒnggòng", "adv. altogether, in total"),
    ("参与", "cānyù", "v. to take part in"),
    ("人员", "rényuán", "n. personnel, staff"),
    ("相对", "xiāngduì", "adj. relative, comparative"),
    ("类型", "lèixíng", "n. type, category"),
    ("称", "chēng", "v. to weigh"),
    ("可靠", "kěkào", "adj. reliable, dependable"),
    ("纳入", "nàrù", "v. to include, to incorporate into"),
    ("分析", "fēnxī", "v. to analyze"),
    ("志愿者", "zhìyuànzhě", "n. volunteer"),
    ("跟踪", "gēnzōng", "v. to follow, to track"),
    ("成果", "chéngguǒ", "n. result, achievement"),
    ("清晰", "qīngxī", "adj. clear, distinct"),
    ("即", "jí", "v./adv. to be; namely"),
    ("升", "shēng", "v. to rise, to go up"),
    ("达到", "dá dào", "v. to reach, to attain"),
    ("意外", "yìwài", "adj./n. unexpected; accident"),
    ("存在", "cúnzài", "v. to exist"),
    ("明显", "míngxiǎn", "adj. obvious, apparent"),
    ("补偿", "bǔcháng", "v. to compensate"),
    ("立即", "lìjí", "adv. immediately, at once"),
    ("趋势", "qūshì", "n. trend, tendency"),
    ("差异", "chāyì", "n. difference"),
    ("联合", "liánhé", "v./adj. to unite, to ally; joint, combined"),
    ("个别", "gèbié", "adv./adj. one or two; exceptional"),
    ("表明", "biǎomíng", "v. to indicate, to manifest"),
    ("临时", "línshí", "adv./adj. for a short time; temporary"),
    ("现象", "xiànxiàng", "n. phenomenon"),
    ("非", "fēi", "v. to be not"),
    ("就餐", "jiùcān", "v. to have one's meal"),
    ("放纵", "fàngzòng", "v. to indulge, to be unrestrained"),
    ("苗条", "miáotiáo", "adj. slim, slender"),
    ("借口", "jièkǒu", "n./v. excuse, pretext; to use as an excuse"),
    ("采取", "cǎiqǔ", "v. to take, to adopt"),
    ("措施", "cuòshī", "n. measure, step"),
]

# ─── HSK 6 Lesson 2: 父母之爱 (Love of parents) ───────────────────────────────
LESSON_HSK6_L2 = [
    ("和蔼", "hé'ǎi", "adj. kindly, amiable"),
    ("和气", "héqi", "adj. gentle, kindly"),
    ("目光", "mùguāng", "n. expression in one's eyes"),
    ("慈祥", "cíxiáng", "adj. (of an elder) kindly, affable"),
    ("跨", "kuà", "v. to step, to stride"),
    ("自主", "zìzhǔ", "v. to decide for oneself, to be one's own master"),
    ("甭", "béng", "adv. don't, needn't"),
    ("脱离", "tuōlí", "v. to break away from, to separate oneself from"),
    ("诱惑", "yòuhuò", "v. to entice, to tempt"),
    ("无比", "wúbǐ", "v. to be incomparable, to be unparalleled"),
    ("向往", "xiàngwǎng", "v. to yearn for, to look forward to"),
    ("孤独", "gūdú", "adj. lonely, solitary"),
    ("哭鼻子", "kū bízi", "to cry, to weep"),
    ("片刻", "piànkè", "n. short while, moment"),
    ("步伐", "bùfá", "n. step, pace"),
    ("包围", "bāowéi", "v. to surround, to encircle"),
    ("感染", "gǎnrǎn", "v. to infect, to affect"),
    ("恨不得", "hènbude", "v. to be anxious to, to be dying or itching to"),
    ("跟前", "gēnqián", "n. in front of, close to"),
    ("团圆", "tuányuán", "v. to reunite"),
    ("近来", "jìnlái", "v. recently, lately"),
    ("酝酿", "yùnniàng", "v. to brew, to ferment, to deliberate (upon)"),
    ("刹那", "chànà", "n. instant, split second"),
    ("反常", "fǎncháng", "adj. unusual, abnormal"),
    ("埋怨", "mányuàn", "v. to complain, to blame"),
    ("体谅", "tǐliàng", "v. to show understanding and sympathy, to make allowances"),
    ("无精打采", "wújīng-dǎcǎi", "listless, in low spirits"),
    ("规划", "guīhuà", "v. to plan"),
    ("熬", "áo", "v. to endure, to hold out"),
    ("漫长", "màncháng", "adj. very long, endless"),
    ("寂静", "jìjìng", "adj. quiet, silent"),
    ("稿件", "gǎojiàn", "n. manuscript, contribution"),
    ("难得", "nándé", "adj. hard to come by, rare"),
    ("心疼", "xīnténg", "v. to feel painful, to be tormented"),
    ("掩饰", "yǎnshì", "v. to cover up, to conceal"),
    ("隐瞒", "yǐnmán", "v. to hide, to conceal"),
    ("唠叨", "láodao", "v. to chatter, to be garrulous"),
    ("吃苦", "chī kǔ", "v. to bear hardships, to suffer"),
    ("欣慰", "xīnwèi", "adj. gratified, satisfied"),
    ("本事", "běnshi", "n. ability, capability"),
    ("皱纹", "zhòuwén", "n. wrinkle"),
    ("顿时", "dùnshí", "adv. at once, instantly"),
    ("不由得", "bùyóude", "adv. can't help (doing sth.)"),
    ("热泪盈眶", "rèlèi yíng kuàng", "one's eyes brimming with tears"),
]

# ─── HSK 6 Lesson 4: 完美的胜利 (A perfect victory) ───────────────────────────
LESSON_HSK6_L4 = [
    ("厌倦", "yànjuàn", "v. to be tired of, to be weary of"),
    ("厌恶", "yànwù", "v. to detest, to loathe"),
    ("倘若", "tǎngruò", "conj. if, supposing"),
    ("发觉", "fājué", "v. to find, to realize"),
    ("承诺", "chéngnuò", "v. to promise"),
    ("草率", "cǎoshuài", "adj. careless, rash"),
    ("对抗", "duìkàng", "v. to resist, to oppose"),
    ("尝试", "chángshì", "v. to try, to attempt"),
    ("盲目", "mángmù", "adj. blind, aimless"),
    ("乘", "chéng", "prep. to avail oneself of, to take advantage of"),
    ("喘气", "chuǎn qì", "v. to breathe deeply, to pant, to gasp"),
    ("饶恕", "ráoshù", "v. to forgive, to pardon"),
    ("强迫", "qiǎngpò", "v. to force, to compel"),
    ("实施", "shíshī", "v. to put into effect, to implement"),
    ("不料", "bùliào", "conj. unexpectedly, to one's surprise"),
    ("猛烈", "měngliè", "adj. fierce, violent"),
    ("子弹", "zǐdàn", "n. bullet"),
    ("蔑视", "mièshì", "v. to despise, to look down upon"),
    ("眼光", "yǎnguāng", "n. look in one's eyes"),
    ("盯", "dīng", "v. to fix one's eyes on, to stare at"),
    ("愤怒", "fènnù", "adj. furious, indignant"),
    ("愚蠢", "yúchǔn", "adj. stupid, foolish"),
    ("家伙", "jiāhuo", "n. fellow, chap, guy"),
    ("可恶", "kěwù", "adj. hateful, detestable"),
    ("耍", "shuǎ", "v. to play, to behave (in an unsavory manner)"),
    ("流氓", "liúmáng", "n. rogue, hoodlum, hooligan"),
    ("扁", "biǎn", "adj. flat"),
    ("夕阳", "xīyáng", "n. setting sun"),
    ("留神", "liú shén", "v. to be careful, to look out"),
    ("打猎", "dǎ liè", "v. to hunt, to go hunting"),
    ("不顾", "búgù", "v. in spite of, regardless of"),
    ("挣扎", "zhēngzhá", "v. to struggle"),
    ("窜", "cuàn", "v. to flee, to scurry"),
    ("摆脱", "bǎituō", "v. to get rid of, to break away from"),
    ("死亡", "sǐwáng", "v. to die"),
    ("恰巧", "qiàqiǎo", "adv. by chance"),
    ("毫无", "háo wú", "not in the least, without"),
    ("抵抗", "dǐkàng", "v. to resist, to fight against"),
    ("部位", "bùwèi", "n. (particularly of the human body) part, position"),
    ("悲惨", "bēicǎn", "adj. miserable, tragic"),
    ("未免", "wèimiǎn", "adv. rather, a bit too"),
    ("残忍", "cánrěn", "adj. cruel, ruthless"),
    ("良心", "liángxīn", "n. conscience"),
    ("锋利", "fēnglì", "adj. sharp, keen"),
    ("缠绕", "chánrào", "v. to twine, to wind"),
    ("耗费", "hàofèi", "v. to use, to consume"),
    ("缺口", "quēkǒu", "n. gap, breach, crack"),
    ("举世瞩目", "jǔshì zhǔmù", "to attract worldwide attention, to draw the attention of the world"),
]

# (level, label, words)
LESSONS = [
    (5, "HSK 5 Lesson 13: 放眼世界", LESSON_HSK5_L13),
    (5, "HSK 5 Lesson 16: 体重与节食", LESSON_HSK5_L16),
    (6, "HSK 6 Lesson 2:  父母之爱", LESSON_HSK6_L2),
    (6, "HSK 6 Lesson 4:  完美的胜利", LESSON_HSK6_L4),
]


def upload_lesson(
    level: int,
    label: str,
    words: list[tuple[str, str, str]],
    *,
    apply: bool,
    api_key: str,
    throttle: float,
) -> dict:
    coll = db.words_col()
    stats = {"total": len(words), "inserted": 0, "skipped": 0, "failed": 0}
    print(f"== {label} (level={level}) ==")
    for chinese, pinyin, english_raw in words:
        english = strip_pos(english_raw)
        existing = coll.find_one(
            {"chinese": chinese, "level": level, "pinyin": pinyin},
            {"_id": 1},
        )
        if existing:
            print(f"  [skip]   {chinese:<6}  {pinyin:<20}  already present")
            stats["skipped"] += 1
            continue

        if not apply:
            print(f"  [preview] {chinese:<6}  {pinyin:<20}  {english}")
            continue

        try:
            mp3 = synthesize_piece_mp3(
                text=chinese,
                voice=VOICE,
                model=MODEL_DEFAULT,
                api_key=api_key,
                speed=1.0,
                tail_pad_seconds=0.3,
            )
        except TTSError as e:
            print(f"  [FAIL]   {chinese:<6}  {pinyin:<20}  TTS error: {e}")
            stats["failed"] += 1
            if throttle > 0:
                time.sleep(throttle)
            continue

        doc = {
            "chinese": chinese,
            "pinyin": pinyin,
            "english": english,
            "level": level,
            "audioBlob": Binary(mp3),
        }
        coll.insert_one(doc)
        print(f"  [OK]     {chinese:<6}  {pinyin:<20}  {len(mp3)//1024}KB  {english}")
        stats["inserted"] += 1
        if throttle > 0:
            time.sleep(throttle)

    print(f"  → total {stats['total']}, inserted {stats['inserted']}, "
          f"skipped {stats['skipped']}, failed {stats['failed']}\n")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="generate audio and insert into DB (default: dry-run)")
    ap.add_argument("--throttle", type=float, default=1.0,
                    help="seconds to sleep between TTS calls (default 1.0)")
    ap.add_argument("--api-key", default="",
                    help="DashScope API key (falls back to DASHSCOPE_API_KEY env var / .env)")
    args = ap.parse_args()

    api_key = (args.api_key or DASHSCOPE_API_KEY or os.getenv("DASHSCOPE_API_KEY", "")).strip()
    if args.apply and not api_key:
        print("ERROR: DashScope API key missing.", file=sys.stderr)
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] uploading lessons (voice={VOICE}, throttle={args.throttle}s)")
    print(f"  DB: {db.DB_NAME}\n")

    grand = {"total": 0, "inserted": 0, "skipped": 0, "failed": 0}
    for level, label, words in LESSONS:
        stats = upload_lesson(
            level, label, words,
            apply=args.apply, api_key=api_key, throttle=args.throttle,
        )
        for k in grand:
            grand[k] += stats[k]

    print(f"Summary: {grand['total']} total, {grand['inserted']} inserted, "
          f"{grand['skipped']} skipped, {grand['failed']} failed.")
    if not args.apply:
        print("Dry-run only (no TTS, no writes). Re-run with --apply to generate audio + insert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
