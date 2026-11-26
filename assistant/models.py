"""Help guide improvement records.

- UnansweredQuestion: a question the assistant could not answer, or whose
  answer the person marked "This did not help". Saved only when
  ASSISTANT_COLLECT_UNANSWERED is on, with names, references, emails, and long
  numbers masked, the asker's role but not their identity, and deleted after
  ASSISTANT_UNANSWERED_RETENTION_DAYS.
- HelpPhrase: another way of asking for a help article ("Also asked as"),
  added by a reviewer. The assistant matches questions against these phrases
  as well as the article text, so a reworded question finds the right article.
"""

from django.conf import settings
from django.db import models

from core.models import BaseModel


class QuestionReason(models.TextChoices):
    NO_MATCH = 'no_match', 'No article matched'
    NOT_HELPFUL = 'not_helpful', 'Marked "This did not help"'


class QuestionStatus(models.TextChoices):
    OPEN = 'open', 'To review'
    LINKED = 'linked', 'Linked to an article'
    NEW_ARTICLE = 'new_article', 'Answered by a new article'
    OUT_OF_SCOPE = 'out_of_scope', 'Out of scope'


class UnansweredQuestion(BaseModel):
    text = models.CharField(max_length=500)
    reason = models.CharField(max_length=20, choices=QuestionReason.choices)
    asker_role = models.CharField(max_length=60)
    closest_article = models.CharField(max_length=200, blank=True)
    similarity = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=QuestionStatus.choices,
                              default=QuestionStatus.OPEN)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'assistant_unanswered_questions'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return self.text[:60]


class HelpPhrase(BaseModel):
    article_title = models.CharField(max_length=200)
    text = models.CharField(max_length=500)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'assistant_help_phrases'
        ordering = ['article_title', 'text']
        constraints = [models.UniqueConstraint(fields=['article_title', 'text'],
                                               name='help_phrase_unique_per_article')]

    def __str__(self):
        return f'{self.article_title}: {self.text[:40]}'
