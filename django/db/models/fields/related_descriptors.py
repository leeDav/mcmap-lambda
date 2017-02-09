"""
Accessors for related objects.

When a field defines a relation between two models, each model class provides
an attribute to access related instances of the other model class (unless the
reverse accessor has been disabled with related_name='+').

Accessors are implemented as descriptors in order to customize access and
assignment. This module defines the descriptor classes.

Forward accessors follow foreign keys. Reverse accessors trace them back. For
example, with the following models::

    class Parent(Model):
        pass

    class Child(Model):
        parent = ForeignKey(Parent, related_name='children')

 ``child.parent`` is a forward many-to-one relation. ``parent.children`` is a
reverse many-to-one relation.

There are three types of relations (many-to-one, one-to-one, and many-to-many)
and two directions (forward and reverse) for a total of six combinations.

1. Related instance on the forward side of a many-to-one or one-to-one
   relation: ``ForwardManyToOneDescriptor``.

   Uniqueness of foreign key values is irrelevant to accessing the related
   instance, making the many-to-one and one-to-one cases identical as far as
   the descriptor is concerned. The constraint is checked upstream (unicity
   validation in forms) or downstream (unique indexes in the database).

   If you're looking for ``ForwardOneToOneDescriptor``, use
   ``ForwardManyToOneDescriptor`` instead.

2. Related instance on the reverse side of a one-to-one relation:
   ``ReverseOneToOneDescriptor``.

   One-to-one relations are asymmetrical, despite the apparent symmetry of the
   name, because they're implemented in the database with a foreign key from
   one table to another. As a consequence ``ReverseOneToOneDescriptor`` is
   slightly different from ``ForwardManyToOneDescriptor``.

3. Related objects manager for related instances on the reverse side of a
   many-to-one relation: ``ReverseManyToOneDescriptor``.

   Unlike the previous two classes, this one provides access to a collection
   of objects. It returns a manager rather than an instance.

4. Related objects manager for related instances on the forward or reverse
   sides of a many-to-many relation: ``ManyToManyDescriptor``.

   Many-to-many relations are symmetrical. The syntax of Django models
   requires declaring them on one side but that's an implementation detail.
   They could be declared on the other side without any change in behavior.
   Therefore the forward and reverse descriptors can be the same.

   If you're looking for ``ForwardManyToManyDescriptor`` or
   ``ReverseManyToManyDescriptor``, use ``ManyToManyDescriptor`` instead.
"""

from __future__ import unicode_literals

import warnings
from operator import attrgetter

from django.db import connections, router, transaction
from django.db.models import Q, signals
from django.db.models.query import QuerySet
from django.utils.deprecation import RemovedInDjango20Warning
from django.utils.functional import cached_property


class ForwardManyToOneDescriptor(object):
    """
    Accessor to the related object on the forward side of a many-to-one or
    one-to-one relation.

    In the example::

        class Child(Model):
            parent = ForeignKey(Parent, related_name='children')

    ``child.parent`` is a ``ForwardManyToOneDescriptor`` instance.
    """

    def __init__(self, field_with_rel):
        self.field = field_with_rel
        self.cache_name = self.field.get_cache_name()

    @cached_property
    def RelatedObjectDoesNotExist(self):
        # The exception can't be created at initialization time since the
        # related model might not be resolved yet; `rel.model` might still be
        # a string model reference.
        return type(
            str('RelatedObjectDoesNotExist'),
            (self.field.remote_field.model.DoesNotExist, AttributeError),
            {}
        )

    def is_cached(self, instance):
        return hasattr(instance, self.cache_name)

    def get_queryset(self, **hints):
        related_model = self.field.remote_field.model

        if getattr(related_model._default_manager, 'use_for_related_fields', False):
            if not getattr(related_model._default_manager, 'silence_use_for_related_fields_deprecation', False):
                warnings.warn(
                    "use_for_related_fields is deprecated, instead "
                    "set Meta.base_manager_name on '{}'.".format(related_model._meta.label),
                    RemovedInDjango20Warning, 2
                )
            manager = related_model._default_manager
        else:
            manager = related_model._base_manager

        return manager.db_manager(hints=hints).all()

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        queryset._add_hints(instance=instances[0])

        rel_obj_attr = self.field.get_foreign_related_value
        instance_attr = self.field.get_local_related_value
        instances_dict = {instance_attr(inst): inst for inst in instances}
        related_field = self.field.foreign_related_fields[0]

        # FIXME: This will need to be revisited when we introduce support for
        # composite fields. In the meantime we take this practical approach to
        # solve a regression on 1.6 when the reverse manager in hidden
        # (related_name ends with a '+'). Refs #21410.
        # The check for len(...) == 1 is a special case that allows the query
        # to be join-less and smaller. Refs #21760.
        if self.field.remote_field.is_hidden() or len(self.field.foreign_related_fields) == 1:
            query = {'%s__in' % related_field.name: set(instance_attr(inst)[0] for inst in instances)}
        else:
            query = {'%s__in' % self.field.related_query_name(): instances}
        queryset = queryset.filter(**query)

        # Since we're going to assign directly in the cache,
        # we must manage the reverse relation cache manually.
        if not self.field.remote_field.multiple:
            rel_obj_cache_name = self.field.remote_field.get_cache_name()
            for rel_obj in queryset:
                instance = instances_dict[rel_obj_attr(rel_obj)]
                setattr(rel_obj, rel_obj_cache_name, instance)
        return queryset, rel_obj_attr, instance_attr, True, self.cache_name

    def __get__(self, instance, cls=None):
        """
        Get the related instance through the forward relation.

        With the example above, when getting ``child.parent``:

        - ``self`` is the descriptor managing the ``parent`` attribute
        - ``instance`` is the ``child`` instance
        - ``cls`` is the ``Child`` class (we don't need it)
        """
        if instance is None:
            return self

        # The related instance is loaded from the database and then cached in
        # the attribute defined in self.cache_name. It can also be pre-cached
        # by the reverse accessor (ReverseOneToOneDescriptor).
        try:
            rel_obj = getattr(instance, self.cache_name)
        except AttributeError:
            val = self.field.get_local_related_value(instance)
            if None in val:
                rel_obj = None
            else:
                qs = self.get_queryset(instance=instance)
                qs = qs.filter(self.field.get_reverse_related_filter(instance))
                # Assuming the database enforces foreign keys, this won't fail.
                rel_obj = qs.get()
                # If this is a one-to-one relation, set the reverse accessor
                # cache on the related object to the current instance to avoid
                # an extra SQL query if it's accessed later on.
                if not self.field.remote_field.multiple:
                    setattr(rel_obj, self.field.remote_field.get_cache_name(), instance)
            setattr(instance, self.cache_name, rel_obj)

        if rel_obj is None and not self.field.null:
            raise self.RelatedObjectDoesNotExist(
                "%s has no %s." % (self.field.model.__name__, self.field.name)
            )
        else:
            return rel_obj

    def __set__(self, instance, value):
        """
        Set the related instance through the forward relation.

        With the example above, when setting ``child.parent = parent``:

        - ``self`` is the descriptor managing the ``parent`` attribute
        - ``instance`` is the ``child`` instance
        - ``value`` is the ``parent`` instance on the right of the equal sign
        """
        # An object must be an instance of the related class.
        if value is not None and not isinstance(value, self.field.remote_field.model._meta.concrete_model):
            raise ValueError(
                'Cannot assign "%r": "%s.%s" must be a "%s" instance.' % (
                    value,
                    instance._meta.object_name,
                    self.field.name,
                    self.field.remote_field.model._meta.object_name,
                )
            )
        elif value is not None:
            if instance._state.db is None:
                instance._state.db = router.db_for_write(instance.__class__, instance=value)
            elif value._state.db is None:
                value._state.db = router.db_for_write(value.__class__, instance=instance)
            elif value._state.db is not None and instance._state.db is not None:
                if not router.allow_relation(value, instance):
                    raise ValueError('Cannot assign "%r": the current database router prevents this relation.' % value)

        # If we're setting the value of a OneToOneField to None, we need to clear
        # out the cache on any old related object. Otherwise, deleting the
        # previously-related object will also cause this object to be deleted,
        # which is wrong.
        if value is None:
            # Look up the previously-related object, which may still be available
            # since we've not yet cleared out the related field.
            # Use the cache directly, instead of the accessor; if we haven't
            # populated the cache, then we don't care - we're only accessing
            # the object to invalidate the accessor cache, so there's no
            # need to populate the cache just to expire it again.
            related = getattr(instance, self.cache_name, None)

            # If we've got an old related object, we need to clear out its
            # cache. This cache also might not exist if the related object
            # hasn't been accessed yet.
            if related is not None:
                setattr(related, self.field.remote_field.get_cache_name(), None)

            for lh_field, rh_field in self.field.related_fields:
                setattr(instance, lh_field.attname, None)

        # Set the values of the related field.
        else:
            for lh_field, rh_field in self.field.related_fields:
                setattr(instance, lh_field.attname, getattr(value, rh_field.attname))

        # Set the related instance cache used by __get__ to avoid an SQL query
        # when accessing the attribute we just set.
        setattr(instance, self.cache_name, value)

        # If this is a one-to-one relation, set the reverse accessor cache on
        # the related object to the current instance to avoid an extra SQL
        # query if it's accessed later on.
        if value is not None and not self.field.remote_field.multiple:
            setattr(value, self.field.remote_field.get_cache_name(), instance)


class ReverseOneToOneDescriptor(object):
    """
    Accessor to the related object on the reverse side of a one-to-one
    relation.

    In the example::

        class Restaurant(Model):
            place = OneToOneField(Place, related_name='restaurant')

    ``place.restaurant`` is a ``ReverseOneToOneDescriptor`` instance.
    """

    def __init__(self, related):
        self.related = related
        self.cache_name = related.get_cache_name()

    @cached_property
    def RelatedObjectDoesNotExist(self):
        # The exception isn't created at initialization time for the sake of
        # consistency with `ForwardManyToOneDescriptor`.
        return type(
            str('RelatedObjectDoesNotExist'),
            (self.related.related_model.DoesNotExist, AttributeError),
            {}
        )

    def is_cached(self, instance):
        return hasattr(instance, self.cache_name)

    def get_queryset(self, **hints):
        related_model = self.related.related_model

        if getattr(related_model._default_manager, 'use_for_related_fields', False):
            if not getattr(related_model._default_manager, 'silence_use_for_related_fields_deprecation', False):
                warnings.warn(
                    "use_for_related_fields is deprecated, instead "
                    "set Meta.base_manager_name on '{}'.".format(related_model._meta.label),
                    RemovedInDjango20Warning, 2
                )
            manager = related_model._default_manager
        else:
            manager = related_model._base_manager

        return manager.db_manager(hints=hints).all()

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        queryset._add_hints(instance=instances[0])

        rel_obj_attr = attrgetter(self.related.field.attname)

        def instance_attr(obj):
            return obj._get_pk_val()

        instances_dict = {instance_attr(inst): inst for inst in instances}
        query = {'%s__in' % self.related.field.name: instances}
        queryset = queryset.filter(**query)

        # Since we're going to assign directly in the cache,
        # we must manage the reverse relation cache manually.
        rel_obj_cache_name = self.related.field.get_cache_name()
        for rel_obj in queryset:
            instance = instances_dict[rel_obj_attr(rel_obj)]
            setattr(rel_obj, rel_obj_cache_name, instance)
        return queryset, rel_obj_attr, instance_attr, True, self.cache_name

    def __get__(self, instance, cls=None):
        """
        Get the related instance through the reverse relation.

        With the example above, when getting ``place.restaurant``:

        - ``self`` is the descriptor managing the ``restaurant`` attribute
        - ``instance`` is the ``place`` instance
        - ``cls`` is the ``Place`` class (unused)

        Keep in mind that ``Restaurant`` holds the foreign key to ``Place``.
        """
        if instance is None:
            return self

        # The related instance is loaded from the database and then cached in
        # the attribute defined in self.cache_name. It can also be pre-cached
        # by the forward accessor (ForwardManyToOneDescriptor).
        try:
            rel_obj = getattr(instance, self.cache_name)
        except AttributeError:
            related_pk = instance._get_pk_val()
            if related_pk is None:
                rel_obj = None
            else:
                filter_args = self.related.field.get_forward_related_filter(instance)
                try:
                    rel_obj = self.get_queryset(instance=instance).get(**filter_args)
                except self.related.related_model.DoesNotExist:
                    rel_obj = None
                else:
                    # Set the forward accessor cache on the related object to
                    # the current instance to avoid an extra SQL query if it's
                    # accessed later on.
                    setattr(rel_obj, self.related.field.get_cache_name(), instance)
            setattr(instance, self.cache_name, rel_obj)

        if rel_obj is None:
            raise self.RelatedObjectDoesNotExist(
                "%s has no %s." % (
                    instance.__class__.__name__,
                    self.related.get_accessor_name()
                )
            )
        else:
            return rel_obj

    def __set__(self, instance, value):
        """
        Set the related instance through the reverse relation.

        With the example above, when setting ``place.restaurant = restaurant``:

        - ``self`` is the descriptor managing the ``restaurant`` attribute
        - ``instance`` is the ``place`` instance
        - ``value`` is the ``restaurant`` instance on the right of the equal sign

        Keep in mind that ``Restaurant`` holds the foreign key to ``Place``.
        """
        # The similarity of the code below to the code in
        # ForwardManyToOneDescriptor is annoying, but there's a bunch
        # of small differences that would make a common base class convoluted.

        if value is None:
            # Update the cached related instance (if any) & clear the cache.
            try:
                rel_obj = getattr(instance, self.cache_name)
            except AttributeError:
                pass
            else:
                delattr(instance, self.cache_name)
                setattr(rel_obj, self.related.field.name, None)
        elif not isinstance(value, self.related.related_model):
            # An object must be an instance of the related class.
            raise ValueError(
                'Cannot assign "%r": "%s.%s" must be a "%s" instance.' % (
                    value,
                    instance._meta.object_name,
                    self.related.get_accessor_name(),
                    self.related.related_model._meta.object_name,
                )
            )
        else:
            if instance._state.db is None:
                instance._state.db = router.db_for_write(instance.__class__, instance=value)
            elif value._state.db is None:
                value._state.db = router.db_for_write(value.__class__, instance=instance)
            elif value._state.db is not None and instance._state.db is not None:
                if not router.allow_relation(value, instance):
                    raise ValueError('Cannot assign "%r": the current database router prevents this relation.' % value)

            related_pk = tuple(getattr(instance, field.attname) for field in self.related.field.foreign_related_fields)
            # Set the value of the related field to the value of the related object's related field
            for index, field in enumerate(self.related.field.local_related_fields):
                setattr(value, field.attname, related_pk[index])

            # Set the related instance cache used by __get__ to avoid an SQL query
            # when accessing the attribute we just set.
            setattr(instance, self.cache_name, value)

            # Set the forward accessor cache on the related object to the current
            # instance to avoid an extra SQL query if it's accessed later on.
            setattr(value, self.related.field.get_cache_name(), instance)


class ReverseManyToOneDescriptor(object):
    """
    Accessor to the related objects manager on the reverse side of a
    many-to-one relation.

    In the example::

        class Child(Model):
            parent = ForeignKey(Parent, related_name='children')

    ``parent.children`` is a ``ReverseManyToOneDescriptor`` instance.

    Most of the implementation is delegated to a dynamically defined manager
    class built by ``create_forward_many_to_many_manager()`` defined below.
    """

    def __init__(self, rel):
        self.rel = rel
        self.field = rel.field

    @cached_property
    def related_manager_cls(self):
        related_model = self.rel.related_model

        return create_reverse_many_to_one_manager(
            related_model._default_manager.__class__,
            self.rel,
        )

    def __get__(self, instance, cls=None):
        """
        Get the related objects through the reverse relation.

        With the example above, when getting ``parent.children``:

        - ``self`` is the descriptor managing the ``children`` attribute
        - ``instance`` is the ``parent`` instance
        - ``cls`` is the ``Parent`` class (unused)
        """
        if instance is None:
            return self

        return self.related_manager_cls(instance)

    def _get_set_deprecation_msg_params(self):
        return (  # RemovedInDjango20Warning
            'reverse side of a related set',
            self.rel.get_accessor_name(),
        )

    def __set__(self, instance, value):
        """
        Set the related objects through the reverse relation.

        With the example above, when setting ``parent.children = children``:

        - ``self`` is the descriptor managing the ``children`` attribute
        - ``instance`` is the ``parent`` instance
        - ``value`` is the ``children`` sequence on the right of the equal sign
        """
        warnings.warn(
            'Direct assignment to the %s is deprecated due to the implicit '
            'save() that happens. Use %s.set() instead.' % self._get_set_deprecation_msg_params(),
            RemovedInDjango20Warning, stacklevel=2,
        )
        manager = self.__get__(instance)
        manager.set(value)


def create_reverse_many_to_one_manager(superclass, rel):
    """
    Create a manager for the reverse side of a many-to-one relation.

    This manager subclasses another manager, generally the default manager of
    the related model, and adds behaviors specific to many-to-one relations.
    """

    class RelatedManager(superclass):
        def __init__(self, instance):
            super(RelatedManager, self).__init__()

            self.instance = instance
            self.model = rel.related_model
            self.field = rel.field

            self.core_filters = {self.field.name: instance}

        def __call__(self, **kwargs):
            # We use **kwargs rather than a kwarg argument to enforce the
            # `manager='manager_name'` syntax.
            manager = getattr(self.model, kwargs.pop('manager'))
            manager_class = create_reverse_many_to_one_manager(manager.__class__, rel)
            return manager_class(self.instance)
        do_not_call_in_templates = True

        def _apply_rel_filters(self, queryset):
            """
            Filter the queryset for the instance this manager is bound to.
            """
            db = self._db or router.db_for_read(self.model, instance=self.instance)
            empty_strings_as_null = connections[db].features.interprets_empty_strings_as_nulls
            queryset._add_hints(instance=self.instance)
            if self._db:
                queryset = queryset.using(self._db)
            queryset = queryset.filter(**self.core_filters)
            for field in self.field.foreign_related_fields:
                val = getattr(self.instance, field.attname)
                if val is None or (val == '' and empty_strings_as_null):
                    return queryset.none()
            queryset._known_related_objects = {self.field: {self.instance.pk: self.instance}}
            return queryset

        def get_queryset(self):
            try:
                return self.instance._prefetched_objects_cache[self.field.related_query_name()]
            except (AttributeError, KeyError):
                queryset = super(RelatedManager, self).get_queryset()
                return self._apply_rel_filters(queryset)

        def get_prefetch_queryset(self, instances, queryset=None):
            if queryset is None:
                queryset = super(RelatedManager, self).get_queryset()

            queryset._add_hints(instance=instances[0])
            queryset = queryset.using(queryset._db or self._db)

            rel_obj_attr = self.field.get_local_related_value
            instance_attr = self.field.get_foreign_related_value
            instances_dict = {instance_attr(inst): inst for inst in instances}
            query = {'%s__in' % self.field.name: instances}
            queryset = queryset.filter(**query)

            # Since we just bypassed this class' get_queryset(), we must manage
            # the reverse relation manually.
            for rel_obj in queryset:
                instance = instances_dict[rel_obj_attr(rel_obj)]
                setattr(rel_obj, self.field.name, instance)
            cache_name = self.field.related_query_name()
            return queryset, rel_obj_attr, instance_attr, False, cache_name

        def add(self, *objs, **kwargs):
            bulk = kwargs.pop('bulk', True)
            objs = list(objs)
            db = router.db_for_write(self.model, instance=self.instance)

            def check_and_update_obj(obj):
                if not isinstance(obj, self.model):
                    raise TypeError("'%s' instance expected, got %r" % (
                        self.model._meta.object_name, obj,
                    ))
                setattr(obj, self.field.name, self.instance)

            if bulk:
                pks = []
                for obj in objs:
                    check_and_update_obj(obj)
                    if obj._state.adding or obj._state.db != db:
                        raise ValueError(
                            "%r instance isn't saved. Use bulk=False or save "
                            "the object first." % obj
                        )
                    pks.append(obj.pk)
                self.model._base_manager.using(db).filter(pk__in=pks).update(**{
                    self.field.name: self.instance,
                })
            else:
                with transaction.atomic(using=db, savepoint=False):
                    for obj in objs:
                        check_and_update_obj(obj)
                        obj.save()
        add.alters_data = True

        def create(self, **kwargs):
            kwargs[self.field.name] = self.instance
            db = router.db_for_write(self.model, instance=self.instance)
            return super(RelatedManager, self.db_manager(db)).create(**kwargs)
        create.alters_data = True

        def get_or_create(self, **kwargs):
            kwargs[self.field.name] = self.instance
            db = router.db_for_write(self.model, instance=self.instance)
            return super(RelatedManager, self.db_manager(db)).get_or_create(**kwargs)
        get_or_create.alters_data = True

        def update_or_create(self, **kwargs):
            kwargs[self.field.name] = self.instance
            db = router.db_for_write(self.model, instance=self.instance)
            return super(RelatedManager, self.db_manager(db)).update_or_create(**kwargs)
        update_or_create.alters_data = True

        # remove() and clear() are only provided if the ForeignKey can have a value of null.
        if rel.field.null:
            def remove(self, *objs, **kwargs):
                if not objs:
                    return
                bulk = kwargs.pop('bulk', True)
                val = self.field.get_foreign_related_value(self.instance)
                old_ids = set()
                for obj in objs:
                    # Is obj actually part of this descriptor set?
                    if self.field.get_local_related_value(obj) == val:
                        old_ids.add(obj.pk)
                    else:
                        raise self.field.remote_field.model.DoesNotExist(
                            "%r is not related to %r." % (obj, self.instance)
                        )
                self._clear(self.filter(pk__in=old_ids), bulk)
            remove.alters_data = True

            def clear(self, **kwargs):
                bulk = kwargs.pop('bulk', True)
                self._clear(self, bulk)
            clear.alters_data = True

            def _clear(self, queryset, bulk):
                db = router.db_for_write(self.model, instance=self.instance)
                queryset = queryset.using(db)
                if bulk:
                    # `QuerySet.update()` is intrinsically atomic.
                    queryset.update(**{self.field.name: None})
                else:
                    with transaction.atomic(using=db, savepoint=False):
                        for obj in queryset:
                            setattr(obj, self.field.name, None)
                            obj.save(update_fields=[self.field.name])
            _clear.alters_data = True

        def set(self, objs, **kwargs):
            # Force evaluation of `objs` in case it's a queryset whose value
            # could be affected by `manager.clear()`. Refs #19816.
            objs = tuple(objs)

            bulk = kwargs.pop('bulk', True)
            clear = kwargs.pop('clear', False)

            if self.field.null:
                db = router.db_for_write(self.model, instance=self.instance)
                with transaction.atomic(using=db, savepoint=False):
                    if clear:
                        self.clear()
                        self.add(*objs, bulk=bulk)
                    else:
                        old_objs = set(self.using(db).all())
                        new_objs = []
                        for obj in objs:
                            if obj in old_objs:
                                old_objs.remove(obj)
                            else:
                                new_objs.append(obj)

                        self.remove(*old_objs, bulk=bulk)
                        self.add(*new_objs, bulk=bulk)
            else:
                self.add(*objs, bulk=bulk)
        set.alters_data = True

    return RelatedManager


class ManyToManyDescriptor(ReverseManyToOneDescriptor):
    """
    Accessor to the related objects manager on the forward and reverse sides of
    a many-to-many relation.

    In the example::

        class Pizza(Model):
            toppings = ManyToManyField(Topping, related_name='pizzas')

    ``pizza.toppings`` and ``topping.pizzas`` are ``ManyToManyDescriptor``
    instances.

    Most of the implementation is delegated to a dynamically defined manager
    class built by ``create_forward_many_to_many_manager()`` defined below.
    """

    def __init__(self, rel, reverse=False):
        super(ManyToManyDescriptor, self).__init__(rel)

        self.reverse = reverse

    @property
    def through(self):
        # through is provided so that you have easy access to the through
        # model (Book.authors.through) for inlines, etc. This is done as
        # a property to ensure that the fully resolved value is returned.
        return self.rel.through

    @cached_property
    def related_manager_cls(self):
        related_model = self.rel.related_model if self.reverse else self.rel.model

        return create_forward_many_to_many_manager(
            related_model._default_manager.__class__,
            self.rel,
            reverse=self.reverse,
        )

    def _get_set_deprecation_msg_params(self):
        return (  # RemovedInDjango20Warning
            '%s side of a many-to-many set' % ('reverse' if self.reverse else 'forward'),
            self.rel.get_accessor_name() if self.reverse else self.field.name,
        )


def create_forward_many_to_many_manager(superclass, rel, reverse):
    """
    Create a manager for the either side of a many-to-many relation.

    This manager subclasses another manager, generally the default manager of
    the related model, and adds behaviors specific to many-to-many relations.
    """

    class ManyRelatedManager(superclass):
        def __init__(self, instance=None):
            super(ManyRelatedManager, self).__init__()

            self.instance = instance

            if not reverse:
                self.model = rel.model
                self.query_field_name = rel.field.related_query_name()
                self.prefetch_cache_name = rel.field.name
                self.source_field_name = rel.field.m2m_field_name()
                self.target_field_name = rel.field.m2m_reverse_field_name()
                self.symmetrical = rel.symmetrical
            else:
                self.model = rel.related_model
                self.query_field_name = rel.field.name
                self.prefetch_cache_name = rel.field.related_query_name()
                self.source_field_name = rel.field.m2m_reverse_field_name()
                self.target_field_name = rel.field.m2m_field_name()
                self.symmetrical = False

            self.through = rel.through
            self.reverse = reverse

            self.source_field = self.through._meta.get_field(self.source_field_name)
            self.target_field = self.through._meta.get_field(self.target_field_name)

            self.core_filters = {}
            for lh_field, rh_field in self.source_field.related_fields:
                core_filter_key = '%s__%s' % (self.query_field_name, rh_field.name)
                self.core_filters[core_filter_key] = getattr(instance, rh_field.attname)

            self.related_val = self.source_field.get_foreign_related_value(instance)
            if None in self.related_val:
                raise ValueError('"%r" needs to have a value for field "%s" before '
                                 'this many-to-many relationship can be used.' %
                                 (instance, self.source_field_name))
            # Even if this relation is not to pk, we require still pk value.
            # The wish is that the instance has been already saved to DB,
            # although having a pk value isn't a guarantee of that.
            if instance.pk is None:
                raise ValueError("%r instance needs to have a primary key value before "
                                 "a many-to-many relationship can be used." %
                                 instance.__class__.__name__)

        def __call__(self, **kwargs):
            # We use **kwargs rather than a kwarg argument to enforce the
            # `manager='manager_name'` syntax.
            manager = getattr(self.model, kwargs.pop('manager'))
            manager_class = create_forward_many_to_many_manager(manager.__class__, rel, reverse)
            return manager_class(instance=self.instance)
        do_not_call_in_templates = True

        def _build_remove_filters(self, removed_vals):
            filters = Q(**{self.source_field_name: self.related_val})
            # No need to add a subquery condition if removed_vals is a QuerySet without
            # filters.
            removed_vals_filters = (not isinstance(removed_vals, QuerySet) or
                                    removed_vals._has_filters())
            if removed_vals_filters:
                filters &= Q(**{'%s__in' % self.target_field_name: removed_vals})
            if self.symmetrical:
                symmetrical_filters = Q(**{self.target_field_name: self.related_val})
                if removed_vals_filters:
                    symmetrical_filters &= Q(
                        **{'%s__in' % self.source_field_name: removed_vals})
                filters |= symmetrical_filters
            return filters

        def _apply_rel_filters(self, queryset):
            """
            Filter the queryset for the instance this manager is bound to.
            """
            queryset._add_hints(instance=self.instance)
            if self._db:
                queryset = queryset.using(self._db)
            return queryset._next_is_sticky().filter(**self.core_filters)

        def get_queryset(self):
            try:
                return self.instance._prefetched_objects_cache[self.prefetch_cache_name]
            except (AttributeError, KeyError):
                queryset = super(ManyRelatedManager, self).get_queryset()
                return self._apply_rel_filters(queryset)

        def get_prefetch_queryset(self, instances, queryset=None):
            if queryset is None:
                queryset = super(ManyRelatedManager, self).get_queryset()

            queryset._add_hints(instance=instances[0])
            queryset = queryset.using(queryset._db or self._db)

            query = {'%s__in' % self.query_field_name: instances}
            queryset = queryset._next_is_sticky().filter(**query)

            # M2M: need to annotate the query in order to get the primary model
            # that the secondary model was actually related to. We know that
            # there will already be a join on the join table, so we can just add
            # the select.

            # For non-autocreated 'through' models, can't assume we are
            # dealing with PK values.
            fk = self.through._meta.get_field(self.source_field_name)
            join_table = self.through._meta.db_table
            connection = connections[queryset.db]
            qn = connection.ops.quote_name
            queryset = queryset.extra(select={
                '_prefetch_related_val_%s' % f.attname:
                '%s.%s' % (qn(join_table), qn(f.column)) for f in fk.local_related_fields})
            return (
                queryset,
                lambda result: tuple(
                    getattr(result, '_prefetch_related_val_%s' % f.attname)
                    for f in fk.local_related_fields
                ),
                lambda inst: tuple(
                    f.get_db_prep_value(getattr(inst, f.attname), connection)
                    for f in fk.foreign_related_fields
                ),
                False,
                self.prefetch_cache_name,
            )

        def add(self, *objs):
            if not rel.through._meta.auto_created:
                opts = self.through._meta
                raise AttributeError(
                    "Cannot use add() on a ManyToManyField which specifies an "
                    "intermediary model. Use %s.%s's Manager instead." %
                    (opts.app_label, opts.object_name)
                )

            db = router.db_for_write(self.through, instance=self.instance)
            with transaction.atomic(using=db, savepoint=False):
                self._add_items(self.source_field_name, self.target_field_name, *objs)

                # If this is a symmetrical m2m relation to self, add the mirror entry in the m2m table
                if self.symmetrical:
                    self._add_items(self.target_field_name, self.source_field_name, *objs)
        add.alters_data = True

        def remove(self, *objs):
            if not rel.through._meta.auto_created:
                opts = self.through._meta
                raise AttributeError(
                    "Cannot use remove() on a ManyToManyField which specifies "
                    "an intermediary model. Use %s.%s's Manager instead." %
                    (opts.app_label, opts.object_name)
                )
            self._remove_items(self.source_field_name, self.target_field_name, *objs)
        remove.alters_data = True

        def clear(self):
            db = router.db_for_write(self.through, instance=self.instance)
            with transaction.atomic(using=db, savepoint=False):
                signals.m2m_changed.send(
                    sender=self.through, action="pre_clear",
                    instance=self.instance, reverse=self.reverse,
                    model=self.model, pk_set=None, using=db,
                )
                filters = self._build_remove_filters(super(ManyRelatedManager, self).get_queryset().using(db))
                self.through._default_manager.using(db).filter(filters).delete()

                signals.m2m_changed.send(
                    sender=self.through, action="post_clear",
                    instance=self.instance, reverse=self.reverse,
                    model=self.model, pk_set=None, using=db,
                )
        clear.alters_data = True

        def set(self, objs, **kwargs):
            if not rel.through._meta.auto_created:
                opts = self.through._meta
                raise AttributeError(
                    "Cannot set values on a ManyToManyField which specifies an "
                    "intermediary model. Use %s.%s's Manager instead." %
                    (opts.app_label, opts.object_name)
                )

            # Force evaluation of `objs` in case it's a queryset whose value
            # could be affected by `manager.clear()`. Refs #19816.
            objs = tuple(objs)

            clear = kwargs.pop('clear', False)

            db = router.db_for_write(self.through, instance=self.instance)
            with transaction.atomic(using=db, savepoint=False):
                if clear:
                    self.clear()
                    self.add(*objs)
                else:
                    old_ids = set(self.using(db).values_list(self.target_field.target_field.attname, flat=True))

                    new_objs = []
                    for obj in objs:
                        fk_val = (
                            self.target_field.get_foreign_related_value(obj)[0]
                            if isinstance(obj, self.model) else obj
                        )
                        if fk_val in old_ids:
                            old_ids.remove(fk_val)
                        else:
                            new_objs.append(obj)

                    self.remove(*old_ids)
                    self.add(*new_objs)
        set.alters_data = True

        def create(self, **kwargs):
            # This check needs to be done here, since we can't later remove this
            # from the method lookup table, as we do with add and remove.
            if not self.through._meta.auto_created:
                opts = self.through._meta
                raise AttributeError(
                    "Cannot use create() on a ManyToManyField which specifies "
                    "an intermediary model. Use %s.%s's Manager instead." %
                    (opts.app_label, opts.object_name)
                )
            db = router.db_for_write(self.instance.__class__, instance=self.instance)
            new_obj = super(ManyRelatedManager, self.db_manager(db)).create(**kwargs)
            self.add(new_obj)
            return new_obj
        create.alters_data = True

        def get_or_create(self, **kwargs):
            db = router.db_for_write(self.instance.__class__, instance=self.instance)
            obj, created = super(ManyRelatedManager, self.db_manager(db)).get_or_create(**kwargs)
            # We only need to add() if created because if we got an object back
            # from get() then the relationship already exists.
            if created:
                self.add(obj)
            return obj, created
        get_or_create.alters_data = True

        def update_or_create(self, **kwargs):
            db = router.db_for_write(self.instance.__class__, instance=self.instance)
            obj, created = super(ManyRelatedManager, self.db_manager(db)).update_or_create(**kwargs)
            # We only need to add() if created because if we got an object back
            # from get() then the relationship already exists.
            if created:
                self.add(obj)
            return obj, created
        update_or_create.alters_data = True

        def _add_items(self, source_field_name, target_field_name, *objs):
            # source_field_name: the PK fieldname in join table for the source object
            # target_field_name: the PK fieldname in join table for the target object
            # *objs - objects to add. Either object instances, or primary keys of object instances.

            # If there aren't any objects, there is nothing to do.
            from django.db.models import Model
            if objs:
                new_ids = set()
                for obj in objs:
                    if isinstance(obj, self.model):
                        if not router.allow_relation(obj, self.instance):
                            raise ValueError(
                                'Cannot add "%r": instance is on database "%s", value is on database "%s"' %
                                (obj, self.instance._state.db, obj._state.db)
                            )
                        fk_val = self.through._meta.get_field(
                            target_field_name).get_foreign_related_value(obj)[0]
                        if fk_val is None:
                            raise ValueError(
                                'Cannot add "%r": the value for field "%s" is None' %
                                (obj, target_field_name)
                            )
                        new_ids.add(fk_val)
                    elif isinstance(obj, Model):
                        raise TypeError(
                            "'%s' instance expected, got %r" %
                            (self.model._meta.object_name, obj)
                        )
                    else:
                        new_ids.add(obj)

                db = router.db_for_write(self.through, instance=self.instance)
                vals = (self.through._default_manager.using(db)
                        .values_list(target_field_name, flat=True)
                        .filter(**{
                            source_field_name: self.related_val[0],
                            '%s__in' % target_field_name: new_ids,
                        }))
                new_ids = new_ids - set(vals)

                with transaction.atomic(using=db, savepoint=False):
                    if self.reverse or source_field_name == self.source_field_name:
                        # Don't send the signal when we are inserting the
                        # duplicate data row for symmetrical reverse entries.
                        signals.m2m_changed.send(
                            sender=self.through, action='pre_add',
                            instance=self.instance, reverse=self.reverse,
                            model=self.model, pk_set=new_ids, using=db,
                        )

                    # Add the ones that aren't there already
                    self.through._default_manager.using(db).bulk_create([
                        self.through(**{
                            '%s_id' % source_field_name: self.related_val[0],
                            '%s_id' % target_field_name: obj_id,
                        })
                        for obj_id in new_ids
                    ])

                    if self.reverse or source_field_name == self.source_field_name:
                        # Don't send the signal when we are inserting the
                        # duplicate data row for symmetrical reverse entries.
                        signals.m2m_changed.send(
                            sender=self.through, action='post_add',
                            instance=self.instance, reverse=self.reverse,
                            model=self.model, pk_set=new_ids, using=db,
                        )

        def _remove_items(self, source_field_name, target_field_name, *objs):
            # source_field_name: the PK colname in join table for the source object
            # target_field_name: the PK colname in join table for the target object
            # *objs - objects to remove
            if not objs:
                return

            # Check that all the objects are of the right type
            old_ids = set()
            for obj in objs:
                if isinstance(obj, self.model):
                    fk_val = self.target_field.get_foreign_related_value(obj)[0]
                    old_ids.add(fk_val)
                else:
                    old_ids.add(obj)

            db = router.db_for_write(self.through, instance=self.instance)
            with transaction.atomic(using=db, savepoint=False):
                # Send a signal to the other end if need be.
                signals.m2m_changed.send(
                    sender=self.through, action="pre_remove",
                    instance=self.instance, reverse=self.reverse,
                    model=self.model, pk_set=old_ids, using=db,
                )
                target_model_qs = super(ManyRelatedManager, self).get_queryset()
                if target_model_qs._has_filters():
                    old_vals = target_model_qs.using(db).filter(**{
                        '%s__in' % self.target_field.target_field.attname: old_ids})
                else:
                    old_vals = old_ids
                filters = self._build_remove_filters(old_vals)
                self.through._default_manager.using(db).filter(filters).delete()

                signals.m2m_changed.send(
                    sender=self.through, action="post_remove",
                    instance=self.instance, reverse=self.reverse,
                    model=self.model, pk_set=old_ids, using=db,
                )

    return ManyRelatedManager
