import os

from django.db import models
from django.db.models import signals
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _

from django.forms import forms

from epubsearcher import EpubWorker

from celery.task import task

from autoslug import AutoSlugField

import logging
logger = logging.getLogger(__name__)



class EPUBFileField(models.FileField):
    '''
    Validates that the file is a Epub
    '''
    def clean(self, *args, **kwargs):
        data = super(EPUBFileField, self).clean(*args, **kwargs)
        book_name = data.file.name

        if not book_name[-4:] == 'epub':
            raise forms.ValidationError(_('Not a Epub File'))
        return data

from django.core.exceptions import ValidationError

def validate_epub(data):
    book_name = data.file.name
    if not book_name[-4:] == 'epub':
        raise ValidationError(_('%s is not a Epub File') % book_name)

class AuthorModel(models.Model):
    name = models.CharField(max_length=200)
    
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _('author')
        verbose_name_plural = _('authors')


class BookModel(models.Model):
    title = models.CharField(max_length=300, verbose_name=_('title'))
    authors = models.ManyToManyField(AuthorModel, verbose_name=_('authors'))
    book = models.FileField(upload_to='books/books',
                            verbose_name=_('book file'),
                            validators=[validate_epub])
    file_name = models.CharField(max_length=300, blank=True)
    slug = AutoSlugField(populate_from='title')
    added = models.DateTimeField(auto_now_add=True)
    cover = models.ImageField(upload_to='books/covers',
                              default='books/covers/defaults.png',
                              verbose_name=_('cover'))
    # description = models.TextField(max_length=600, default=_('no description'))

    class Meta:
        verbose_name = _('book')
        verbose_name_plural = _('books')

    def __str__(self):
        return self.title

    @property
    def is_chapters_connected_to_book(self):
        return bool(self.chapterbookmodel_set.all())

    # def save(self, *args, **kwargs):
    #     # if not self.id:
    #     #     # Newly created object, so set slug
    #     #     self.slug = slugify(self.title)
    #     super(BookModel, self).save(*args, **kwargs)


class ChapterBookModel(models.Model):
    chapter_cfi = models.CharField(max_length=100)
    book = models.ForeignKey(BookModel)

    def __str__(self):
        return self.chapter_cfi

    class Meta:
        verbose_name = _('chapter')
        verbose_name_plural = _('chapters')

@task(ignore_result=True)
def async_add_chapters_to_book(instance, *args, **kwargs):
    if not instance.book:
        logger.info("Not book loaded")
        return None

    if not instance.file_name:  #TODO: fix runs twice
        instance.file_name = instance.book.name.lstrip("./")
        instance.save()

    book_address = instance.book.file.name
    worker = EpubWorker(book_address)
    chapters_cfi = worker.get_chapters_cfi()
    for cfi in chapters_cfi:
        ch = ChapterBookModel(chapter_cfi=cfi, book=instance)
        ch.save()
    logger.info("Chapters {} for book {} were saved".format(chapters_cfi, instance.title))
    return True

signals.post_save.connect(async_add_chapters_to_book.delay, BookModel)

# These two auto-delete files from filesystem when they are unneeded:
@receiver(models.signals.post_delete, sender=BookModel)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """Deletes file from filesystem
    when corresponding `BookModel` object is deleted.
    """
    def remove_if_exist(fields=[]):
        for field in fields:
            if field:
                if os.path.isfile(field.path):
                    os.remove(field.path)

    remove_if_exist(fields=[instance.book, instance.cover])

@receiver(models.signals.pre_save, sender=BookModel)
def auto_delete_file_on_change(sender, instance, **kwargs):
    """Deletes file from filesystem
    when corresponding `BookModel` object is changed.
    """
    if not instance.pk:
        return False


    try:
        old_object = BookModel.objects.get(pk=instance.pk)
    except BookModel.DoesNotExist:
        return False

    new_cover = instance.cover
    if not old_object.cover.name == 'books/covers/defaults.png' and not old_object.cover == new_cover:
        if os.path.isfile(old_object.cover.path):
            os.remove(old_object.cover.path)

    new_book = instance.book
    if not old_object.book == new_book:
        if os.path.isfile(old_object.book.path):
            os.remove(old_object.book.path)