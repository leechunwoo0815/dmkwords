#!/usr/bin/env python3
"""
DmkWords 测试数据种子脚本
创建完整的测试数据，覆盖所有业务场景

用法:
    venv/bin/python -m backend.seeds.seed_test_data
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from backend.database import _get_engine
from backend.domain.activity.models import Activity
from backend.domain.advancement.models import (
    Achievement,
    ChildAchievement,
    ChildLevel,
    Level,
    QuestionBank,
)
from backend.domain.book.models import Book
from backend.domain.bookshelf.models import Bookshelf
from backend.domain.child.models import Child
from backend.domain.message.models import SystemMessage
from backend.domain.reading.models import BookPage, ReadingProgress
from backend.domain.user.models import User

logger = logging.getLogger(__name__)


def seed():
    engine = _get_engine()
    Session = sessionmaker(bind=engine)
    db = Session()

    logger.info("🌱 开始创建测试数据...")

    # ==================== 1. 用户和孩子 ====================
    user = db.query(User).filter(User.phone == "13800000001").first()
    if not user:
        user = User(openid="test_openid_demo", phone="13800000001")
        db.add(user)
        db.flush()
    logger.info(f"  ✅ 用户: id={user.id}")

    child = db.query(Child).filter(Child.user_id == user.id).first()
    if not child:
        child = Child(
            user_id=user.id,
            name="小明",
            english_name="Tom",
            age=7,
            grade="二年级",
            status=Child.STATUS_OFFICIAL,
            member_start_time=datetime.now() - timedelta(days=90),
            member_expire_time=datetime.now() + timedelta(days=275),
            total_words_read=42000,
            total_books_finished=8,
            current_streak_days=7,
            longest_streak_days=15,
        )
        db.add(child)
        db.flush()
    logger.info(f"  ✅ 孩子: {child.name}({child.english_name}), 状态={child.status}")

    # ==================== 2. 级别 ====================
    levels = db.query(Level).filter(Level.is_deleted == 0).order_by(Level.sort_order).all()
    if not levels:
        level_data = [
            ("A", 1, 10, "🌱"),
            ("B", 2, 10, "🌿"),
            ("C", 3, 10, "🍀"),
            ("D", 4, 15, "🌳"),
            ("E", 5, 15, "🌲"),
            ("F", 6, 15, "🌾"),
        ]
        for name, order, req_books, emoji in level_data:
            db.add(
                Level(
                    name=name,
                    sort_order=order,
                    required_books=req_books,
                    required_quiz_pass_rate=Decimal("0.80"),
                    badge_emoji=emoji,
                )
            )
        db.flush()
        levels = db.query(Level).filter(Level.is_deleted == 0).order_by(Level.sort_order).all()

    cl = db.query(ChildLevel).filter(ChildLevel.child_id == child.id, ChildLevel.is_current).first()
    if not cl:
        cl = ChildLevel(
            child_id=child.id,
            level_id=levels[0].id,
            is_current=True,
            books_read_at_level=5,
            quizzes_passed_at_level=3,
        )
        db.add(cl)
        db.flush()
    logger.info(f"  ✅ 级别: {levels[0].name}级, 已读{cl.books_read_at_level}本")

    # ==================== 3. 图书（6本） ====================
    books_data = [
        {
            "isbn": "978-0-06-440055-8",
            "title": "Charlotte's Web",
            "author": "E.B. White",
            "ar_value": 4.4,
            "word_count": 31836,
            "age_min": 7,
            "age_max": 11,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "Chapter 1: Before Breakfast\n\n'Where's Papa going with that ax?' said Fern to her mother as they were setting the table for breakfast.\n\n'Out to the hoghouse,' replied Mrs. Arable. 'Some pigs were born last night.'\n\n'I don't see why he needs an ax,' continued Fern, who was only eight.\n\n'Well,' said her mother, 'one of the pigs is a runt. It's very small and weak, and it will never amount to anything. So your father has decided to do away with it.'",
                "Chapter 2: Charlotte\n\nThe next day was rainy and dark. Rain fell on the roof of the barn and dripped steadily from the eaves. Rain fell in the yard and ran in crooked courses down into the lane.\n\n'Charlotte,' said Wilbur, after a while, 'why are you so quiet?'\n\n'I like to keep still,' she said. 'I've always been rather quiet.'",
                "Chapter 3: Escape\n\nThe summer days were long and wonderful. Fern no longer spent all her time at the barn. She was growing up, and her parents felt she should be doing other things besides hanging around a pigpen.\n\nBut Wilbur was not lonely. He had many visitors.",
                "Chapter 4: The Web\n\n'Salutations!' cried Wilbur.\n\n'What?' said the voice.\n\n'Salutations!' repeated Wilbur.\n\n'What are they, and where are you?' screamed the voice. 'Please, please, please tell me where you are.'",
                "Chapter 5: The Miracle\n\nNext morning, when Fern came downstairs, her mother was in the kitchen making breakfast.\n\n'Fern,' she said gently, 'I have something to tell you. Your father and I have decided that you're old enough to take on more responsibility.'\n\n'What kind of responsibility?' asked Fern.\n\n'We'd like you to start helping more around the house and spend less time at the barn.'",
            ],
        },
        {
            "isbn": "978-0-00-715106-4",
            "title": "The Cat in the Hat",
            "author": "Dr. Seuss",
            "ar_value": 2.1,
            "word_count": 1624,
            "age_min": 4,
            "age_max": 8,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "The sun did not shine.\nIt was too wet to play.\nSo we sat in the house\nAll that cold, cold, wet day.\n\nI sat there with Sally.\nWe sat there, we two.\nAnd I said, 'How I wish\nWe had something to do!'",
                "Too wet to go out\nAnd too cold to play ball.\nSo we sat in the house.\nWe did nothing at all.\n\nSo all we could do was to\nSit! Sit! Sit! Sit!\nAnd we did not like it.\nNot one little bit.",
                "And then something went bump!\nHow that bump made us jump!\nWe looked!\nThen we saw him step in on the mat!\nWe looked!\nAnd we saw him!\nThe Cat in the Hat!",
                "'I know it is wet\nAnd the sun is not sunny.\nBut we can have\nLots of good fun that is funny!'\n\n'I know some good games we could play,'\nSaid the cat.\n'I know some new tricks,'\nSaid the Cat in the Hat.",
                "'Look at me!\nLook at me now!'\nSaid the cat.\n'With a cup and a cake\nOn the top of my hat!'\n\n'I can hold up TWO books!\nI can hold up the ship!'\n\nAnd then Sally and I\nSaw him run out the door.",
            ],
        },
        {
            "isbn": "978-0-394-80016-5",
            "title": "Green Eggs and Ham",
            "author": "Dr. Seuss",
            "ar_value": 1.5,
            "word_count": 820,
            "age_min": 3,
            "age_max": 7,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "I am Sam.\nSam I am.\n\nThat Sam-I-am!\nThat Sam-I-am!\nI do not like\nThat Sam-I-am!",
                "Do you like\nGreen eggs and ham?\n\nI do not like them,\nSam-I-am.\nI do not like\nGreen eggs and ham.",
                "Would you like them\nHere or there?\n\nI would not like them\nHere or there.\nI would not like them\nAnywhere.\nI do not like\nGreen eggs and ham.\nI do not like them,\nSam-I-am.",
                "Would you eat them\nIn a box?\nWould you eat them\nWith a fox?\n\nNot in a box.\nNot with a fox.\nNot in a house.\nNot with a mouse.",
                "Say!\nIn the dark?\nHere in the dark!\nWould you, could you, in the dark?\n\nI would not, could not,\nIn the dark.\n\nI do not like them,\nSam-I-am.\nI do not like\nGreen eggs and ham.\n\nI DO so like\nGreen eggs and ham!\nThank you!\nThank you,\nSam-I-am!",
            ],
        },
        {
            "isbn": "978-0-06-024902-3",
            "title": "Goodnight Moon",
            "author": "Margaret Wise Brown",
            "ar_value": 1.8,
            "word_count": 131,
            "age_min": 2,
            "age_max": 5,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "In the great green room\nThere was a telephone\nAnd a red balloon\nAnd a picture of—\nThe cow jumping over the moon",
                "And there were three little bears sitting on chairs\nAnd two little kittens\nAnd a pair of mittens\nAnd a little toyhouse\nAnd a young mouse",
                "And a comb and a brush and a bowl full of mush\nAnd a quiet old lady who was whispering 'hush'\n\nGoodnight room\nGoodnight moon",
                "Goodnight cow jumping over the moon\nGoodnight light\nAnd the red balloon\nGoodnight bears\nGoodnight chairs\n\nGoodnight nobody\nGoodnight mush",
                "Goodnight to the old lady whispering 'hush'\nGoodnight stars\nGoodnight air\nGoodnight noises everywhere\n\nThe End.",
            ],
        },
        {
            "isbn": "978-0-06-443178-6",
            "title": "Where the Wild Things Are",
            "author": "Maurice Sendak",
            "ar_value": 3.2,
            "word_count": 1018,
            "age_min": 4,
            "age_max": 8,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "The night Max wore his wolf suit and made mischief of one kind\nand another\nhis mother called him 'WILD THING!'\nand Max said 'I'LL EAT YOU UP!'\nso he was sent to bed without eating anything.",
                "That very night in Max's room a forest grew\nand grew—\nand grew until his ceiling hung with vines\nand the walls became the world all around.",
                "And an ocean tumbled by with a private boat for Max\nand he sailed off through night and day\nand in and out of weeks\nand almost over a year\nto where the wild things are.",
                "And when he came to the place where the wild things are\nthey roared their terrible roars and gnashed their terrible teeth\nand rolled their terrible eyes and showed their terrible claws.",
                "And Max said 'BE STILL!'\nand tamed them with the magic trick\nof staring into all their yellow eyes without blinking once\nand they were frightened and called him the most wild thing of all.",
            ],
        },
        {
            "isbn": "978-0-399-22690-8",
            "title": "The Very Hungry Caterpillar",
            "author": "Eric Carle",
            "ar_value": 2.9,
            "word_count": 224,
            "age_min": 2,
            "age_max": 6,
            "series_name": None,
            "has_audio": False,
            "pages": [
                "In the light of the moon a little egg lay on a leaf.\n\nOne Sunday morning the warm sun came up and—pop!—out of the egg came a tiny and very hungry caterpillar.",
                "He started to look for some food.\n\nOn Monday he ate through one apple. But he was still hungry.\n\nOn Tuesday he ate through two pears. But he was still hungry.",
                "On Wednesday he ate through three plums. But he was still hungry.\n\nOn Thursday he ate through four strawberries. But he was still hungry.\n\nOn Friday he ate through five oranges. But he was still hungry.",
                "On Saturday he ate through one piece of chocolate cake, one ice-cream cone, one pickle, one slice of Swiss cheese, one slice of salami, one lollipop, one piece of cherry pie, one sausage, one cupcake, and one slice of watermelon.\nThat night he had a stomachache!",
                "The next day was Sunday again. The caterpillar ate through one nice green leaf, and after that he felt much better.\n\nNow he wasn't hungry any more—and he wasn't a little caterpillar any more. He was a big, fat caterpillar.\n\nHe built a small house, called a cocoon, around himself. He stayed inside for more than two weeks. Then he nibbled a hole in the cocoon and pushed his way out.\n\nHe was a beautiful butterfly!",
            ],
        },
    ]

    created_books = []
    for bd in books_data:
        existing = db.query(Book).filter(Book.isbn == bd["isbn"]).first()
        if existing:
            created_books.append(existing)
            continue
        book = Book(
            isbn=bd["isbn"],
            title=bd["title"],
            author=bd["author"],
            ar_value=bd["ar_value"],
            word_count=bd["word_count"],
            age_min=bd["age_min"],
            age_max=bd["age_max"],
            series_name=bd["series_name"],
            has_audio=bd["has_audio"],
        )
        db.add(book)
        db.flush()

        # 创建书页
        for i, text in enumerate(bd["pages"], 1):
            db.add(BookPage(book_id=book.id, page_number=i, text_content=text, content_type=0))
        created_books.append(book)
    db.commit()
    logger.info(f"  ✅ 图书: {len(created_books)} 本（含页面内容）")

    # ==================== 4. 题库（每本书5道题） ====================
    questions_data = {
        "Charlotte's Web": [
            (
                "What did Fern's father have when he went out?",
                "A rake",
                "An ax",
                "A shovel",
                "A hammer",
                "B",
            ),
            (
                "What was the name of the pig?",
                "Wilbur",
                "Charlotte",
                "Templeton",
                "Fern",
                "A",
            ),
            (
                "What kind of animal was Charlotte?",
                "A pig",
                "A rat",
                "A spider",
                "A goose",
                "C",
            ),
            (
                "Where did Wilbur live?",
                "In a house",
                "In a barn",
                "In a garden",
                "In a forest",
                "B",
            ),
            (
                "Who saved Wilbur's life?",
                "Fern",
                "Charlotte",
                "Templeton",
                "The farmer",
                "B",
            ),
        ],
        "The Cat in the Hat": [
            (
                "What was the weather like at the beginning?",
                "Sunny",
                "Rainy",
                "Snowy",
                "Windy",
                "B",
            ),
            ("Who came to visit?", "A dog", "A bird", "A cat", "A fish", "C"),
            (
                "What did the cat balance on his hat?",
                "A ball",
                "A cup and cake",
                "A book",
                "A fish",
                "B",
            ),
            (
                "What were the names of the two things?",
                "Thing 1 and Thing 2",
                "Fish 1 and Fish 2",
                "Cat 1 and Cat 2",
                "Book 1 and Book 2",
                "A",
            ),
            (
                "How did the story end?",
                "The cat stayed forever",
                "The cat cleaned up and left",
                "The house was destroyed",
                "The fish was happy",
                "B",
            ),
        ],
        "Green Eggs and Ham": [
            (
                "What does Sam-I-am offer?",
                "Red eggs",
                "Green eggs and ham",
                "Blue eggs",
                "Yellow eggs",
                "B",
            ),
            (
                "Where does Sam NOT suggest eating them?",
                "In a box",
                "With a fox",
                "On the moon",
                "In a house",
                "C",
            ),
            (
                "What is the other character's attitude at first?",
                "Happy",
                "Hungry",
                "Refusing",
                "Sleepy",
                "C",
            ),
            (
                "Does the character try the green eggs and ham?",
                "Yes, he loves them",
                "No, never",
                "Yes, but he hates them",
                "Only the eggs",
                "A",
            ),
            (
                "What lesson does the story teach?",
                "Don't eat green food",
                "Try new things",
                "Listen to Sam",
                "Cooking is fun",
                "B",
            ),
        ],
    }

    for book in created_books:
        existing_q = (
            db.query(QuestionBank)
            .filter(QuestionBank.book_id == book.id, QuestionBank.is_deleted == 0)
            .first()
        )
        if existing_q:
            continue
        qs = questions_data.get(book.title)
        if not qs:
            continue
        for qt, oa, ob, oc, od, ans in qs:
            db.add(
                QuestionBank(
                    book_id=book.id,
                    question_text=qt,
                    option_a=oa,
                    option_b=ob,
                    option_c=oc,
                    option_d=od,
                    correct_answer=ans,
                    difficulty=1,
                )
            )
    db.commit()
    logger.info("  ✅ 题库: 每本书5道选择题（含答案）")

    # ==================== 5. 书架和收藏 ====================
    for i, book in enumerate(created_books[:5]):
        existing = (
            db.query(Bookshelf)
            .filter(
                Bookshelf.child_id == child.id,
                Bookshelf.book_id == book.id,
                Bookshelf.is_deleted == 0,
            )
            .first()
        )
        if not existing:
            db.add(
                Bookshelf(
                    child_id=child.id,
                    book_id=book.id,
                    status=Bookshelf.STATUS_BORROWING,
                )
            )
    db.commit()
    logger.info("  ✅ 书架: 5本想读")

    # ==================== 6. 阅读进度 ====================
    for i, book in enumerate(created_books[:3]):
        existing = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == child.id, ReadingProgress.book_id == book.id)
            .first()
        )
        if not existing:
            total_pages = len(book.title) % 5 + 5  # 5-10 pages
            current = total_pages - 1 if i == 0 else total_pages // 2
            db.add(
                ReadingProgress(
                    child_id=child.id,
                    book_id=book.id,
                    current_page=current,
                    total_pages=total_pages,
                    progress_pct=round(current / total_pages * 100, 2),
                    is_finished=1 if current >= total_pages else 0,
                )
            )
    db.commit()
    logger.info("  ✅ 阅读进度: 3本书")

    # ==================== 7. 成就 ====================
    ach_data = [
        ("读完10本书", Achievement.TYPE_BOOK_MILESTONE, "📚"),
        ("首次满分", Achievement.TYPE_QUIZ_PERFECT, "🏅"),
        ("连续7天打卡", Achievement.TYPE_STREAK, "⭐"),
        ("词汇达人", Achievement.TYPE_BOOK_MILESTONE, "🏆"),
        ("精准答题", Achievement.TYPE_QUIZ_PERFECT, "🎯"),
        ("A级达成", Achievement.TYPE_LEVEL_UP, "🌱"),
    ]
    achievements = []
    for name, atype, emoji in ach_data:
        existing = db.query(Achievement).filter(Achievement.name == name).first()
        if existing:
            achievements.append(existing)
            continue
        ach = Achievement(name=name, type=atype, badge_emoji=emoji)
        db.add(ach)
        db.flush()
        achievements.append(ach)

    for ach in achievements[:3]:
        existing = (
            db.query(ChildAchievement)
            .filter(
                ChildAchievement.child_id == child.id,
                ChildAchievement.achievement_id == ach.id,
            )
            .first()
        )
        if not existing:
            db.add(ChildAchievement(child_id=child.id, achievement_id=ach.id))
    db.commit()
    logger.info(f"  ✅ 成就: {len(achievements)}个定义, 孩子已获3个")

    # ==================== 8. 系统消息 ====================
    msg_data = [
        ("欢迎加入 DmkWords", "欢迎成为正式会员！开始你的英文阅读之旅吧。", 1),
        ("会员续费提醒", "您的会员将在30天后到期，建议提前续费。", 5),
        ("新书上架通知", "本月新增10本英文原版图书，快来图书馆看看吧！", 2),
    ]
    for title, content, msg_type in msg_data:
        existing = db.query(SystemMessage).filter(SystemMessage.title == title).first()
        if not existing:
            db.add(SystemMessage(user_id=user.id, title=title, content=content, msg_type=msg_type))
    db.commit()
    logger.info(f"  ✅ 系统消息: {len(msg_data)}条")

    # ==================== 9. 活动 ====================
    activity = db.query(Activity).first()
    if not activity:
        db.add(
            Activity(
                title="暑期阅读挑战赛",
                description="连续阅读30天，赢取精美奖品！",
                type=Activity.TYPE_READING,
                status=Activity.STATUS_ENROLLING,
                enroll_deadline=datetime.now() + timedelta(days=7),
                start_time=datetime.now() + timedelta(days=14),
                end_time=datetime.now() + timedelta(days=44),
                max_participants=50,
            )
        )
        db.commit()
    logger.info("  ✅ 活动: 1个")

    logger.info("\n🎉 测试数据创建完成！")
    logger.info(f"   用户ID: {user.id}, 孩子ID: {child.id}")
    logger.info(f"   图书: {len(created_books)}本, 题库: 每本5题")
    logger.info(f"   书架: 5本, 收藏: {len(created_books) - 5}本")
    logger.info(f"   成就: {len(achievements)}个, 消息: {len(msg_data)}条")

    db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
