# MIT License
#
# Copyright (C) IBM Corporation 2019
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
Implementation of the standard exponential mechanism, and its derivative, the hierarchical mechanism.
"""
from numbers import Real

import numpy as np

from diffprivlib.mechanisms.base import DPMechanism
from diffprivlib.mechanisms.binary import Binary
from diffprivlib.utils import copy_docstring


class Exponential(DPMechanism):
    r"""
    The exponential mechanism for achieving differential privacy on categorical inputs, as first proposed by McSherry
    and Talwar.

    The exponential mechanism achieves differential privacy by randomly choosing an output value for a given input
    value, with greater probability given to values 'closer' to the input, as measured by a given utility function.

    Paper link: https://www.cs.drexel.edu/~greenie/privacy/mdviadp.pdf

    Parameters
    ----------
    epsilon : float
        Privacy parameter :math:`\epsilon` for the mechanism.  Must be in (0, ∞].

    utility_list : list of tuples
        The utility list of the mechanism.  Must be specified as a list of tuples, of the form ("value1", "value2",
        utility), where each `value` is a string and `utility` is a strictly positive float.  A `utility` must be
        specified for every pair of values given in the `utility_list`.

    """
    def __init__(self, *, epsilon, utility_list):
        super().__init__(epsilon=epsilon, delta=0.0)

        self._balanced_tree = False
        self._utility_values, self._sensitivity, self._domain_values = self._build_utility(utility_list)
        self._check_utility_full(self._domain_values)
        self._normalising_constant = self._build_normalising_constant()

    def _build_utility(self, utility_list):
        if not isinstance(utility_list, list):
            raise TypeError("Utility must be given in a list")

        self._normalising_constant = None

        utility_values = {}
        domain_values = []
        sensitivity = 0

        for _utility_sub_list in utility_list:
            value1, value2, utility_value = _utility_sub_list

            if not isinstance(value1, str) or not isinstance(value2, str):
                raise TypeError("Utility keys must be strings")
            if not isinstance(utility_value, Real):
                raise TypeError("Utility value must be a number")
            if utility_value < 0.0:
                raise ValueError("Utility values must be non-negative")

            sensitivity = max(sensitivity, utility_value)
            if value1 not in domain_values:
                domain_values.append(value1)
            if value2 not in domain_values:
                domain_values.append(value2)

            if value1 == value2:
                continue
            if value1 < value2:
                utility_values[(value1, value2)] = utility_value
            else:
                utility_values[(value2, value1)] = utility_value

        self._utility_values = utility_values
        self._sensitivity = sensitivity
        self._domain_values = domain_values

        return utility_values, sensitivity, domain_values

    def _check_utility_full(self, domain_values):
        missing = []

        for val1 in domain_values:
            for val2 in domain_values:
                if val1 >= val2:
                    continue

                if (val1, val2) not in self._utility_values:
                    missing.append((val1, val2))

        if missing:
            raise ValueError("Utility values missing: %s" % (str(missing)))

        return True

    @property
    def utility_list(self):
        """Gets the utility list of the mechanism, in the same form as accepted by `.set_utility_list`.

        Returns
        -------
        utility_list : list of tuples (str, str, float), or None
            Returns a list of tuples of the form ("value1", "value2", utility), or `None` if the utility has not yet
            been set.

        """
        utility_list = []

        for _key, _utility in self._utility_values.items():
            value1, value2 = _key
            utility_list.append((value1, value2, _utility))

        return utility_list

    def _build_normalising_constant(self, re_eval=False):
        balanced_tree = True
        first_constant_value = None
        normalising_constant = {}

        for _base_leaf in self._domain_values:
            constant_value = 0.0

            for _target_leaf in self._domain_values:
                constant_value += self._get_prob(_base_leaf, _target_leaf)

            normalising_constant[_base_leaf] = constant_value

            if first_constant_value is None:
                first_constant_value = constant_value
            elif not np.isclose(constant_value, first_constant_value):
                balanced_tree = False

        # If the tree is balanced, we can eliminate the doubling factor
        if balanced_tree and not re_eval:
            self._balanced_tree = True
            return self._build_normalising_constant(True)

        return normalising_constant

    def _get_utility(self, value1, value2):
        if value1 == value2:
            return 0

        if value1 > value2:
            return self._get_utility(value1=value2, value2=value1)

        return self._utility_values[(value1, value2)]

    def _get_prob(self, value1, value2):
        if value1 == value2:
            return 1.0

        balancing_factor = 1 if self._balanced_tree else 2
        return np.exp(- self.epsilon * self._get_utility(value1, value2) / balancing_factor / self._sensitivity)

    def _check_all(self, value):
        super()._check_all(value)

        if not isinstance(value, str):
            raise TypeError("Value to be randomised must be a string")

        if value not in self._domain_values:
            raise ValueError("Value \"%s\" not in domain" % value)

        return True

    @classmethod
    def _check_epsilon_delta(cls, epsilon, delta):
        if not delta == 0:
            raise ValueError("Delta must be zero")

        return super()._check_epsilon_delta(epsilon, delta)

    @copy_docstring(DPMechanism.bias)
    def bias(self, value):
        raise NotImplementedError

    @copy_docstring(DPMechanism.variance)
    def variance(self, value):
        raise NotImplementedError

    @copy_docstring(Binary.randomise)
    def randomise(self, value):
        self._check_all(value)

        unif_rv = self._rng.random() * self._normalising_constant[value]
        cum_prob = 0
        _target_value = None

        for _target_value in self._normalising_constant.keys():
            cum_prob += self._get_prob(value, _target_value)

            if unif_rv <= cum_prob:
                return _target_value

        return _target_value


class ExponentialHierarchical(Exponential):
    r"""
    Adaptation of the exponential mechanism to hierarchical data.  Simplifies the process of specifying utility values,
    as the values can be inferred from the hierarchy.

    Parameters
    ----------
    epsilon : float
        Privacy parameter :math:`\epsilon` for the mechanism.  Must be in (0, ∞].

    hierarchy : nested list of str
        The hierarchy as specified as a nested list of string.  Each string must be a leaf node, and each leaf node
        must lie at the same depth in the hierarchy.

    Examples
    --------
    Example hierarchies:

    >>> flat_hierarchy = ["A", "B", "C", "D", "E"]
    >>> nested_hierarchy = [["A"], ["B"], ["C"], ["D", "E"]]

    """
    def __init__(self, *, epsilon, hierarchy):
        self.hierarchy = hierarchy
        utility_list = self._build_utility_list(self._build_hierarchy(hierarchy))
        super().__init__(epsilon=epsilon, utility_list=utility_list)
        self._list_hierarchy = None

    def _build_hierarchy(self, nested_list, parent_node=None):
        if not isinstance(nested_list, list):
            raise TypeError("Hierarchy must be a list")

        if parent_node is None:
            parent_node = []

        hierarchy = {}

        for _i, _value in enumerate(nested_list):
            if isinstance(_value, str):
                hierarchy[_value] = parent_node + [_i]
            elif not isinstance(_value, list):
                raise TypeError("All leaves of the hierarchy must be a string " +
                                "(see node " + (parent_node + [_i]).__str__() + ")")
            else:
                hierarchy.update(self._build_hierarchy(_value, parent_node + [_i]))

        self._check_hierarchy_height(hierarchy)

        return hierarchy

    @staticmethod
    def _check_hierarchy_height(hierarchy):
        hierarchy_height = None
        for _value, _hierarchy_locator in hierarchy.items():
            if hierarchy_height is None:
                hierarchy_height = len(_hierarchy_locator)
            elif len(_hierarchy_locator) != hierarchy_height:
                raise ValueError("Leaves of the hierarchy must all be at the same level " +
                                 "(node %s is at level %d instead of hierarchy height %d)" %
                                 (_hierarchy_locator.__str__(), len(_hierarchy_locator), hierarchy_height))

    @staticmethod
    def _build_utility_list(hierarchy):
        if not isinstance(hierarchy, dict):
            raise TypeError("Hierarchy for _build_utility_list must be a dict")

        utility_list = []
        hierarchy_height = None

        for _root_value, _root_hierarchy_locator in hierarchy.items():
            if hierarchy_height is None:
                hierarchy_height = len(_root_hierarchy_locator)

            for _target_value, _target_hierarchy_locator in hierarchy.items():
                if _root_value >= _target_value:
                    continue

                i = 0
                while (i < len(_root_hierarchy_locator) and
                       _root_hierarchy_locator[i] == _target_hierarchy_locator[i]):
                    i += 1

                utility_list.append([_root_value, _target_value, hierarchy_height - i])

        return utility_list

    @copy_docstring(DPMechanism.bias)
    def bias(self, value):
        raise NotImplementedError

    @copy_docstring(DPMechanism.variance)
    def variance(self, value):
        raise NotImplementedError
